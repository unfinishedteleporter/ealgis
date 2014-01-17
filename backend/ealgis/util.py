
import subprocess as sp
import psycopg2, re, os, os.path, itertools, sys, itertools

def pairwise(iterable):
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = itertools.tee(iterable)
    next(b, None)
    return itertools.izip(a, b)

def piperun(*cmds, **kwargs):
    capture = kwargs.get('capture', False)
    pipes = []
    assert(len(cmds) > 1)
    pipes.append(sp.Popen(cmds[0], stdout=sp.PIPE))
    for cmd, out in zip(cmds[1:], [sp.PIPE]*(len(cmds[1:]) - 1) + [sp.PIPE if capture else None]):
        last = pipes[-1]
        pipes.append(sp.Popen(cmd, stdin=last.stdout, stdout=out, stderr=out))
        last.stdout.close()
    stdin, stderr = pipes[-1].communicate()
    return stdin, stderr, pipes[-1].returncode

def alistdir(path):
    return (os.path.join(path, t) for t in os.listdir(path))

table_name_re = re.compile(r'^[A-Za-z0-9_]+$')
def table_name_valid(table_name):
    return table_name_re.match(table_name) is not None
