#!/usr/bin/env python
# -*- coding: utf-8 -*-

from collections import namedtuple
import cairo, colorsys, csv
from cStringIO import StringIO
from util import pairwise

RGBBase = namedtuple('RGB', ['r', 'g', 'b'])

class RGB(RGBBase):
    def __mul__(self, s):
        return RGB(self.r * s, self.g * s, self.b * s)

    def __div__(self, s):
        return RGB(self.r / s, self.g / s, self.b / s)

    def __sub__(self, rgb):
        return RGB(self.r - rgb.r, self.g - rgb.g, self.b - rgb.b)

    def __add__(self, rgb):
        return RGB(self.r + rgb.r, self.g + rgb.g, self.b + rgb.b)

class ColourScale(object):
    def __init__(self, interpolated, nanno, is_discrete, scale_min=0., scale_max=1.):
        "colour scale @interpolated in [0, 1] normalised, regular.. first is for < scale_min, last is for >= scale_max"
        self.interpolated = interpolated
        self.nlevels = len(self.interpolated)
        self.nanno = nanno
        self.set_scale(scale_min, scale_max)
        self.is_discrete = is_discrete

    @classmethod
    def with_scale_or_flip(cls, scale, scale_min, scale_max, scale_flip):
        new_interp = scale.interpolated + []
        if scale_flip:
            new_interp = list(reversed(new_interp))
        new_scale = ColourScale(
            new_interp,
            scale.nanno,
            scale.is_discrete,
            scale_min,
            scale_max)
        return new_scale

    def set_scale(self, scale_min, scale_max):
        self.scale_min = float(scale_min)
        self.scale_max = float(scale_max)
        # matrices to go in and out of that space
        self.to_scale = cairo.Matrix()
        self.to_scale.translate(scale_min, 0)
        self.to_scale.scale(scale_max - scale_min, 1)
        self.normalise = cairo.Matrix(*self.to_scale)
        self.normalise.invert()

    def get_nlevels(self):
        return self.nlevels

    def lookup(self, v, normalise=True):
        "v -> [0, 1]"
        if normalise:
            v, _ = self.normalise.transform_point(v, 0)
        if v < 0:
            idx = 0
        elif v >= 1:
            idx = self.nlevels - 1
        else:
            idx = int(v * (self.nlevels - 2)) + 1 # eg. 5 levels, 3 "inner"
        return self.interpolated[idx]

    def legend(self, height=400, width=160):
        img = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(img)

        # padding
        PAD_Y = 10
        ATTACH_W = 3

        legend_from = PAD_Y
        legend_to = height - PAD_Y
        legend_height = legend_to - legend_from
        legend_width = width/3
        cr.set_line_width(1)
        # draw box around the colour scale
        cr.move_to(0, PAD_Y + 0.5) # top
        cr.line_to(legend_width + 1, PAD_Y + 0.5)
        cr.move_to(legend_width + 1.5, PAD_Y) # right side
        cr.line_to(legend_width + 1.5, PAD_Y + 1 + legend_height)
        cr.move_to(legend_width + 2, PAD_Y + 1.5 + legend_height) # bottom
        cr.line_to(0, 1.5 + PAD_Y + legend_height)
        cr.move_to(0.5, PAD_Y + 1 + legend_height) # left side
        cr.line_to(0.5, PAD_Y + 1)
        cr.stroke()
        # draw the colour scale; two of the levels extend "beyond" (0, 1] so 
        # we draw slightly past that range
        v_start = - 1. / (self.nlevels - 2)
        v_end = 1. - v_start
        v_inc = (v_end - v_start) / (legend_height - 1)
        for i in xrange(legend_height):
            v = v_end - (v_inc * i)
            c = self.lookup(v, normalise=False)
            cr.set_source_rgb(c.r, c.g, c.b)
            y = legend_from + i + 1.5
            cr.move_to(1, y)
            cr.line_to(1 + legend_width, y)
            cr.stroke()
        # draw annotation
        cr.set_source_rgb(0, 0, 0)
        cr.select_font_face("Inconsolata", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(12)
        nanno = self.nanno
        if not self.is_discrete and nanno < 3:
            nanno = 3
        def draw_label(v, text):
            y_attach = legend_from + (v_end - v) / v_inc + 1.5
            # draw anchor line
            cr.move_to(legend_width, y_attach)
            cr.line_to(legend_width+2+ATTACH_W, y_attach)
            cr.stroke()
            # draw the text
            (x, y, width, height, dx, dy) = cr.text_extents(text)
            cr.move_to(legend_width+2*ATTACH_W, y_attach+height/2)
            cr.show_text(text)
        def fv(v):
            if int(v) == v:
                return str(int(v))
            else:
                return "%.2f" % v
        anno_inc = (1-v_start)/(nanno-1)
        if self.is_discrete:
            discrete_inc = (self.scale_max - self.scale_min) / (nanno - 2)
        else:
            continuous_inc =(self.scale_max - self.scale_min) / (nanno - 1)
        for i in xrange(nanno):
            if i == 0:
                label = "<%s" % fv(self.scale_min)
            elif i == nanno - 1:
                # fixme:figure how to use ≥
                label = u">=%s" % fv(self.scale_max)
            else:
                if self.is_discrete:
                    f_v = self.scale_min + (i - 1) * discrete_inc
                    t_v = self.scale_min + i * discrete_inc
                    if f_v < 0 and t_v < 0:
                        label = "%s>%s" % (fv(t_v), fv(f_v))
                    else:
                        label = "%s<%s" % (fv(f_v), fv(t_v))
                else:
                    label = "%s" % (fv(self.scale_min + i*continuous_inc))
            v = v_start/2. + i * anno_inc
            draw_label(v, label)
        buf = StringIO()
        img.write_to_png(buf)
        return buf.getvalue()

class ContinuousColourScale(ColourScale):
    def __init__(self, defn, nlevels=255):
        super(ContinuousColourScale, self).__init__(
            list(ContinuousColourScale.interpolate_iter(defn, nlevels)),
            len(defn),
            False)

    @classmethod
    def interpolate_iter(cls, defn, nlevels):
        npairs = len(defn) - 1
        pairinc = 1. / npairs
        nlevel_pair = nlevels // npairs
        nlevel_leftover = nlevels - (nlevel_pair * npairs)
        # pair together the definition
        for idx, (c1, c2) in enumerate(pairwise(defn)):
            # start at the start colour, end at the colour before the next colour level
            # ... except at the last idx, where we must end at the end colour
            ninc = n = nlevel_pair
            if idx == npairs - 1:
                n += nlevel_leftover
                ninc = n - 1
            scale_from = (idx - 1) * pairinc
            scale_to = idx * pairinc
            colour_inc = (c2 - c1) / float(ninc)
            for l in xrange(n):
                yield c1 + (colour_inc * l)

class DiscreteColourScale(ColourScale):
    def __init__(self, defn):
        super(DiscreteColourScale, self).__init__(
            defn,
            len(defn),
            True)

class HLSDiscreteColourScale(DiscreteColourScale):
    def __init__(self, ls, nlevels=10):
        (l, s) = ls
        # circle is defined by [0, 1], eg. 0 and 1 are the same point. so opposites of a is a + 0.5
        defn = []
        # use 2/3 of the circle, so end not too like start
        h_inc = (2/3.) / nlevels
        for i in xrange(nlevels):
            h = i * h_inc
            defn.append(RGB(*colorsys.hls_to_rgb(h, l, s)))
        super(HLSDiscreteColourScale, self).__init__(defn)

class CannedColourDefinitions(object):
    def __init__(self):
        self.defs= {}
        self.huey()
        self.color_brewer()

    def get_json(self):
        r = {}
        for name, nlevels in self.defs:
            if name not in r:
                r[name] = []
            r[name].append(nlevels)
        for name in r:
            r[name].sort()
        return r

    def register(self, name, defn):
        self.defs[(name, defn.get_nlevels())] = defn

    def huey(self):
        for i in xrange(2, 13):
            self.register("Huey", HLSDiscreteColourScale((0.5, 0.8), i))

    def get(self, name, nlevels):
        return self.defs[(name, nlevels)]

    def color_brewer(self):
        data = {}
        def strip_bl(r):
            return [t for t in r if t != '']

        with open('/vagrant/contrib/colorbrewer/ColorBrewer_all_schemes_RGBonly3.csv') as fd:
            rdr = csv.reader(fd)
            header = strip_bl(next(rdr))
            def make_getter(nm, f=str):
                idx = header.index(nm)
                def __getter(row):
                    return f(row[idx])
                return __getter
            color_name = make_getter('ColorName')
            typ = make_getter('Type')
            num_of_colours = make_getter('NumOfColors', int)
            r = make_getter('R', lambda v: int(v)/255.)
            g = make_getter('G', lambda v: int(v)/255.)
            b = make_getter('B', lambda v: int(v)/255.)
            for row in rdr:
                name = color_name(row)
                if name == '':
                    break
                nc = num_of_colours(row)
                colours = []
                colours.append(RGB(r(row), g(row), b(row)))
                for i in xrange(nc - 1):
                    row = next(rdr)
                    colours.append(RGB(r(row), g(row), b(row)))
                self.register(name, DiscreteColourScale(colours))

definitions = CannedColourDefinitions()

def colour_for_layer(defn):
    fill = defn['fill']
    scale_min = float(fill['scale_min'])
    scale_max = float(fill['scale_max'])
    scale_name = fill['scale_name']
    scale_nlevels = int(fill['scale_nlevels'])
    scale_flip = 'scale_flip' in fill and fill['scale_flip']
    scale = ColourScale.with_scale_or_flip(
        definitions.get(scale_name, scale_nlevels),
        scale_min, scale_max,
        scale_flip)
    return scale

if __name__ == '__main__':
    scale = definitions.get("Accent", 8)
    with open('html/accent.png', 'w') as fd:
        fd.write(scale.legend())
    scale = definitions.get("Huey", 8)
    with open('html/huey.png', 'w') as fd:
        fd.write(scale.legend())
    definitions.to_json()
