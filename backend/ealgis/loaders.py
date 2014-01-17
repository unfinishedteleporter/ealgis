import os.path, hashlib, zipfile, glob
from util import piperun, table_name_valid
from subprocess import check_call
from pprint import pprint
from itertools import izip
from seqclassifier import SequenceClassifier
import osr, sys, psycopg2, csv

class LoaderException(Exception):
    pass

class DirectoryAccess(object):
    def __init__(self, directory):
        self._directory = directory

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        pass

    def getdir(self):
        return self._directory

    def get(self, filename):
        return os.path.join(self._directory, filename)

    def glob(self, pattern):
        return glob.glob(os.path.join(self.getdir(), pattern))

class ZipAccess(DirectoryAccess):
    def __init__(self, parent, tmpdir, zf_path):
        self._parent, self._tmpdir, self._zf_path = parent, tmpdir, zf_path
        self._unpacked = False
        dpath = os.path.join(self._tmpdir, hashlib.sha1(zf_path.encode('utf8')).hexdigest())
        super(ZipAccess, self).__init__(dpath)

    def _unpack(self):
        if self._parent is not None:
            zf_path = self._parent.get(self._zf_path)
        else:
            zf_path = self._zf_path
        with open(zf_path, 'rb') as fd:
            with zipfile.ZipFile(fd) as zf:
                zf.extractall(self.getdir())
        self._unpacked = True

    def get(self, filename):
        if not self._unpacked:
            self._unpack()
        return super(ZipAccess, self).get(filename)

    def glob(self, filename):
        if not self._unpacked:
            self._unpack()
        return super(ZipAccess, self).glob(filename)

    def __exit__(self, type, value, traceback):
        return super(ZipAccess, self).__exit__(type, value, traceback)

class RewrittenCSV(object):
    def __init__(self, tmpdir, csvpath, mutate_row_cb=None):
        if mutate_row_cb is None:
            mutate_row_cb = lambda line, row: row
        self._tmpdir = tmpdir
        self._path = os.path.join(self._tmpdir, hashlib.sha1(csvpath.encode('utf8')).hexdigest() + '.csv')
        with open(csvpath, 'r') as csv_in:
            with open(self._path, 'w') as csv_out:
                r = csv.reader(csv_in)
                w = csv.writer(csv_out)
                w.writerows((mutate_row_cb(line, row) for (line, row) in enumerate(r)))

    def get(self):
        return self._path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.unlink(self._path)

class GeoDataLoader(object):
    def __init__(self):
        pass

class ShapeLoader(GeoDataLoader):
    @classmethod
    def get_shp_base(cls, shppath):
        return os.path.splitext(shppath)[0]

    @classmethod
    def prj_text(cls, shppath):
        # figure out srid code
        shpbase = ShapeLoader.get_shp_base(shppath)
        try:
            with open(shpbase + '.prj') as prj:
                return prj.read()
        except IOError:
            return None

    @classmethod
    def auto_srid(cls, prj_text):
        if prj_text is None:
            return None
        srs = osr.SpatialReference()
        srs.ImportFromESRI([prj_text])
        srs.AutoIdentifyEPSG()
        auto_srid = srs.GetAuthorityCode(None)
        if auto_srid is not None:
            auto_srid = int(auto_srid)
        return auto_srid

    @classmethod
    def tablename_from_shppath(cls, shppath):
        table_name = os.path.splitext(os.path.basename(shppath))[0].replace(" ", "_")
        return table_name.lower()

    def __init__(self, shppath, srid=None, table_name=None):
        self.shppath = shppath
        self.shpbase = ShapeLoader.get_shp_base(shppath)
        self.shpname = os.path.basename(shppath)
        self.table_name = table_name or ShapeLoader.tablename_from_shppath(shppath)
        if not table_name_valid(self.table_name):
            raise LoaderException("table name is `%s' is invalid." % self.table_name)
        prj_text = ShapeLoader.prj_text(shppath)
        auto_srid = ShapeLoader.auto_srid(prj_text)
        if srid is None:
            srid = auto_srid
        if srid is None:
            print >>sys.stderr, "can't determine srid for `%s'" % (self.shpname)
            print >>sys.stderr, "prj text: %s" % prj_text
            raise LoaderException()
        elif auto_srid is not None and srid != auto_srid:
            print >>sys.stderr, "warning: auto srid (%s) does not match provided srid (%s) for `%s'" % (auto_srid, srid, self.shpname)
        self.srid = srid
    
    def load(self, eal):
        if eal.have_table(self.table_name):
            print "already loaded: %s" % (self.table_name)
            return
        shp_cmd = ['shp2pgsql', '-s', str(self.srid), '-I', self.shppath, self.table_name]
        print >>sys.stderr, shp_cmd
        _, _, code = piperun(shp_cmd, ['psql', '-q', eal.dbname()])
        if code != 0:
            raise LoaderException("load of %s failed." % self.shpname)
        # make the meta info
        print "registering, table name is:", self.table_name
        eal.register_table(self.table_name, geom=True, srid=self.srid, gid='gid')

class CSVLoader(GeoDataLoader):
    def __init__(self, table_name, csvpath, pkey_column=None):
        self.table_name = table_name
        self.csvpath = csvpath
        self.pkey_column = pkey_column

    def load(self, eal):
        db = eal.db
        def columns(header, reader, max_rows=None):
            sql_columns = { int : db.Integer, float : db.Float, str: db.Text }
            classifiers = [ SequenceClassifier() for column in header ]
            for i, row in enumerate(r):
                for classifier, value in izip(classifiers, row):
                    classifier.update(value)
                if max_rows is not None and i == max_rows:
                    break
            coldefs = []
            for idx, (column_name, classifier) in enumerate(izip(header, classifiers)):
                ty = sql_columns[classifier.get()]
                make_index = idx == self.pkey_column
                coldefs.append(db.Column(
                    column_name.lower(),
                    ty,
                    index=make_index,
                    unique=make_index,
                    primary_key=make_index))
            return coldefs

        # smell the file, generate a SQLAlchemy table definition 
        # and then make it
        metadata = db.MetaData()
        with open(self.csvpath) as fd:
            r = csv.reader(fd)
            header = next(r)
            cols = columns(header, r)
        table = db.Table(self.table_name, metadata, *cols)
        metadata.create_all(db.engine)

        # this isn't wrapped by SQLAlchemy, so we must do it ourselves; 
        # invoke the Postgres CSV loader
        conn = db.session.connection()
        res = conn.execute('COPY %s FROM %%s CSV HEADER' % (self.table_name), (self.csvpath, ))
        ti = eal.register_table(self.table_name)
        db.session.commit()
        return ti

