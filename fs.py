import os
import simsym
import symtypes
import errno
import model
import signal
import fs_testgen

SFn = simsym.tuninterpreted("SFn")
SInum = simsym.tuninterpreted("SInum")
SDataByte = simsym.tuninterpreted("SDataByte")
SVa = simsym.tuninterpreted("SVa")
SPipeId = simsym.tuninterpreted("SPipeId")

SPid = simsym.SBool
SOffset = simsym.tsynonym("SOffset", simsym.SInt)
class SData(symtypes.tlist(SDataByte, lenType=SOffset)):
    def _declare_assumptions(self, assume):
        super(SData, self)._declare_assumptions(assume)
        assume(self._len <= 16)
SPipe = simsym.tstruct(data = SData,
                       nread = SOffset)
SPipeMap = symtypes.tmap(SPipeId, SPipe)
SFd = simsym.tstruct(ispipe = simsym.SBool,
                     pipeid = SPipeId,
                     pipewriter = simsym.SBool,
                     inum = SInum,
                     off = SOffset)
SFdNum = simsym.tsynonym("SFdNum", simsym.SInt)
SFdMap = symtypes.tdict(SFdNum, SFd)
SVMA = simsym.tstruct(anon = simsym.SBool,
                      writable = simsym.SBool,
                      inum = SInum,
                      # This offset is in pages, not bytes
                      off = SOffset,
                      anondata = SDataByte)
SVaMap = symtypes.tdict(SVa, SVMA)
SProc = symtypes.tstruct(fd_map = SFdMap,
                         va_map = SVaMap)
SDirMap = symtypes.tdict(SFn, SInum)
SNLink = simsym.tsynonym("SNLink", simsym.SInt)
STime = simsym.tsynonym("STime", simsym.SInt)
SInode = simsym.tstruct(data = SData,
                        nlink = SNLink,
                        atime = STime,
                        mtime = STime,
                        ctime = STime,
                        ## XXX Directories impl:
                        # isdir = simsym.SBool,
                        # dirmap = SDirMap,
                       )
SIMap = symtypes.tmap(SInum, SInode)
## XXX Directories impl:
# SPathname = simsym.tstruct(last = SFn)
## XXX Non-directories impl:
SPathname = SFn

## For some types, we override the default handling in
## IsomorphicMatch.  In particular, by default integers are treated as
## concrete-valued, but we often want to treat them as uninterpreted
## or ignore them completely.  isomorphism_types maps from Symbolic
## types to:
##
## - "ignore", if the type should be ignored altogether when
##   enumerating models.
##
## - "equal", if the type should be constrained only on equality (just
##   like an uninterpreted sort).
##
## At present, the Symbolic type can only be a primitive type or a
## synonym for a primitive type, since isomorphism will destructure
## any compound types before checking this.

isomorphism_types = {
    SOffset: "equal",
    SFdNum: "equal",
    # Ignore data bytes or we'll try to enumerate all distinct
    # equivalence classes for file contents.
    SDataByte: "ignore",
}

class Fs(simsym.tstruct(
        i_map=SIMap, proc0=SProc, proc1=SProc, pipes=SPipeMap,
        ## XXX Non-directories impl:
        root_dir=SDirMap)):

    def getproc(self, pid):
        if pid == False:
            return self.proc0
        return self.proc1

    def iused(self, inum):
        dir = SInum.var('dir')
        fn = SFn.var('fn')
        fd = SFdNum.var('fd')
        pid = SPid.var('pid')

        # If we try to simply index into dirmap, its __getitem__
        # won't have access to the supposition that it contains the right
        # key, and throw an exception.  Thus, we use _map directly.
        return simsym.symor([
            ## XXX Directories impl:
            # simsym.exists(dir,
            #     simsym.symand([
            #         self.i_map[dir].isdir,
            #         simsym.exists(fn,
            #             simsym.symand([self.i_map[dir].dirmap.contains(fn),
            #                            self.i_map[dir].dirmap._map[fn] == inum]))])),

            ## XXX Non-directories impl:
            simsym.exists(fn,
                simsym.symand([self.root_dir.contains(fn),
                               self.root_dir._map[fn] == inum])),

            simsym.exists(fd,
                simsym.symand([self.proc0.fd_map.contains(fd),
                               simsym.symnot(self.proc0.fd_map._map[fd].ispipe),
                               self.proc0.fd_map._map[fd].inum == inum])),

            simsym.exists(fd,
                simsym.symand([self.proc1.fd_map.contains(fd),
                               simsym.symnot(self.proc1.fd_map._map[fd].ispipe),
                               self.proc1.fd_map._map[fd].inum == inum])),
            ])

    def add_selfpid(self, pid):
        ## XXX hack due to our simplified PID model
        ## without loss of generality, assume syscall "a" happens in proc0
        if str(pid).startswith('a.'):
            simsym.assume(pid == False)

    def nameiparent(self, pn):
        ## XXX Non-directories impl:
        return 0, self.root_dir, pn

        ## XXX Directories impl:
        # simsym.assume(self.i_map[self.root_inum].isdir)
        # return self.root_inum, self.i_map[self.root_inum].dirmap, pn.last

    # Each model method must return a dictionary representing its the
    # return value of the implemented call.  The keys in these
    # dictionaries correspond to variables declared in the code
    # generated by fs_testgen, with one exception: in order for
    # spec.test to be able to correctly compare these dictionaries,
    # they must be well-typed.  So if, for example, a return value can
    # either be a literal -1 or some symbolic value, we name the
    # latter something like 'r:data' and handle it specially in
    # fs_testgen.

    @model.methodwrap(pn=SPathname,
                      creat=simsym.SBool,
                      excl=simsym.SBool,
                      trunc=simsym.SBool,
                      anyfd=simsym.SBool,
                      pid=SPid,
                      internal_alloc_inum=SInum,
                      internal_ret_fd=SFdNum,
                      internal_time=STime,
                     )
    def open(self, pn, creat, excl, trunc, anyfd, pid,
             internal_alloc_inum, internal_ret_fd, internal_time):
        self.add_selfpid(pid)
        created = False
        anyfd = False
        _, pndirmap, pnlast = self.nameiparent(pn)
        if creat:
            if not pndirmap.contains(pnlast):
                simsym.assume(simsym.symnot(self.iused(internal_alloc_inum)))

                simsym.assume(internal_time > self.i_map[internal_alloc_inum].atime)
                simsym.assume(internal_time > self.i_map[internal_alloc_inum].mtime)
                simsym.assume(internal_time > self.i_map[internal_alloc_inum].ctime)

                inode = self.i_map[internal_alloc_inum]
                inode.data._len = 0
                inode.nlink = 1
                inode.atime = inode.mtime = inode.ctime = internal_time
                pndirmap[pnlast] = internal_alloc_inum

                created = True
            else:
                if excl: return {'r': -1, 'errno': errno.EEXIST}
        if not pndirmap.contains(pnlast):
            return {'r': -1, 'errno': errno.ENOENT}

        inum = pndirmap[pnlast]
        if trunc:
            if not created:
                simsym.assume(internal_time > self.i_map[inum].mtime)
                simsym.assume(internal_time > self.i_map[inum].ctime)
                self.i_map[inum].mtime = internal_time
                self.i_map[inum].ctime = internal_time
            self.i_map[inum].data._len = 0

        simsym.assume(internal_ret_fd >= 0)
        simsym.assume(simsym.symnot(self.getproc(pid).fd_map.contains(internal_ret_fd)))

        ## Lowest FD
        otherfd = SFdNum.var('fd')
        simsym.assume(simsym.symor([anyfd,
            simsym.symnot(simsym.exists(otherfd,
                simsym.symand([otherfd >= 0,
                               otherfd < internal_ret_fd,
                               self.getproc(pid).fd_map.contains(otherfd)])))]))

        fd_data = self.getproc(pid).fd_map.create(internal_ret_fd)
        fd_data.inum = inum
        fd_data.off = 0
        fd_data.ispipe = False

        return {'r': internal_ret_fd}

    @model.methodwrap(pid=SPid,
                      internal_pipeid=SPipeId,
                      internal_fd_r=SFdNum,
                      internal_fd_w=SFdNum,
                      )
    def pipe(self, pid, internal_pipeid, internal_fd_r, internal_fd_w):
        self.add_selfpid(pid)

        xfd = SFdNum.var('xfd')
        simsym.assume(simsym.symnot(simsym.symor([
            simsym.exists(xfd,
                simsym.symand([self.proc0.fd_map.contains(xfd),
                               self.proc0.fd_map._map[xfd].ispipe,
                               self.proc0.fd_map._map[xfd].pipeid == internal_pipeid])),
            simsym.exists(xfd,
                simsym.symand([self.proc1.fd_map.contains(xfd),
                               self.proc1.fd_map._map[xfd].ispipe,
                               self.proc1.fd_map._map[xfd].pipeid == internal_pipeid]))])))

        empty_pipe = self.pipes[internal_pipeid]
        empty_pipe.nread = 0
        empty_pipe.data._len = 0

        ## lowest FD for read end
        simsym.assume(internal_fd_r >= 0)
        simsym.assume(simsym.symnot(self.getproc(pid).fd_map.contains(internal_fd_r)))
        simsym.assume(simsym.symnot(simsym.exists(xfd,
                simsym.symand([xfd >= 0,
                               xfd < internal_fd_r,
                               self.getproc(pid).fd_map.contains(xfd)]))))
        fd_r_data = self.getproc(pid).fd_map.create(internal_fd_r)
        fd_r_data.ispipe = True
        fd_r_data.pipeid = internal_pipeid
        fd_r_data.pipewriter = False

        ## lowest FD for write end
        simsym.assume(internal_fd_w >= 0)
        simsym.assume(simsym.symnot(self.getproc(pid).fd_map.contains(internal_fd_w)))
        simsym.assume(simsym.symnot(simsym.exists(xfd,
                simsym.symand([xfd >= 0,
                               xfd < internal_fd_w,
                               self.getproc(pid).fd_map.contains(xfd)]))))
        fd_w_data = self.getproc(pid).fd_map.create(internal_fd_w)
        fd_w_data.ispipe = True
        fd_w_data.pipeid = internal_pipeid
        fd_w_data.pipewriter = True

        return {'r': 0, 'fds[0]': internal_fd_r, 'fds[1]': internal_fd_w}

    @model.methodwrap(src=SPathname, dst=SPathname,
                      internal_time=STime)
    def rename(self, src, dst, internal_time):
        srcdiri, srcdirmap, srclast = self.nameiparent(src)
        dstdiri, dstdirmap, dstlast = self.nameiparent(dst)
        if not srcdirmap.contains(srclast):
            return {'r': -1, 'errno': errno.ENOENT}
        if srcdiri == dstdiri and srclast == dstlast:
            return {'r': 0}
        if dstdirmap.contains(dstlast):
            dstinum = dstdirmap[dstlast]
        else:
            dstinum = None
        dstdirmap[dstlast] = srcdirmap[srclast]
        del srcdirmap[srclast]
        if dstinum is not None:
            self.i_map[dstinum].nlink = self.i_map[dstinum].nlink - 1
            simsym.assume(internal_time > self.i_map[dstinum].ctime)
            self.i_map[dstinum].ctime = internal_time
        return {'r': 0}

    @model.methodwrap(pn=SPathname, internal_time=STime)
    def unlink(self, pn, internal_time):
        _, dirmap, pnlast = self.nameiparent(pn)
        if not dirmap.contains(pnlast):
            return {'r': -1, 'errno': errno.ENOENT}
        inum = dirmap[pnlast]
        del dirmap[pnlast]
        self.i_map[inum].nlink = self.i_map[inum].nlink - 1
        simsym.assume(internal_time > self.i_map[inum].ctime)
        self.i_map[inum].ctime = internal_time
        return {'r': 0}

    @model.methodwrap(oldpn=SPathname, newpn=SPathname, internal_time=STime)
    def link(self, oldpn, newpn, internal_time):
        olddiri, olddirmap, oldlast = self.nameiparent(oldpn)
        newdiri, newdirmap, newlast = self.nameiparent(newpn)
        if not olddirmap.contains(oldlast):
            return {'r': -1, 'errno': errno.ENOENT}
        if newdirmap.contains(newlast):
            return {'r': -1, 'errno': errno.EEXIST}
        inum = olddirmap[oldlast]
        newdirmap[newlast] = inum
        self.i_map[inum].nlink = self.i_map[inum].nlink + 1
        simsym.assume(internal_time > self.i_map[inum].ctime)
        self.i_map[inum].ctime = internal_time
        return {'r': 0}

    def iread(self, inum, off, time):
        simsym.assume(off >= 0)
        if off >= self.i_map[inum].data._len:
            return {'r': 0}
        if time is not None:
            simsym.assume(time > self.i_map[inum].atime)
            self.i_map[inum].atime = time
        return {'r': 1, 'data': self.i_map[inum].data[off]}

    @model.methodwrap(fd=SFdNum, pid=SPid, internal_time=STime)
    def read(self, fd, pid, internal_time):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        if self.getproc(pid).fd_map[fd].ispipe:
            if self.getproc(pid).fd_map[fd].pipewriter:
                return {'r': -1, 'errno': errno.EBADF}
            pipe = self.pipes[self.getproc(pid).fd_map[fd].pipeid]
            if pipe.data.len() == pipe.nread:
                ## TODO: return EOF if no more writers; otherwise block
                return {'r': 0}
            simsym.assume(pipe.nread < pipe.data.len())
            simsym.assume(pipe.nread >= 0)
            d = pipe.data[pipe.nread]
            pipe.nread = pipe.nread + 1
            return {'r': 1, 'data': d}
        off = self.getproc(pid).fd_map[fd].off
        r = self.iread(self.getproc(pid).fd_map[fd].inum, off, internal_time)
        if 'data' in r:
            self.getproc(pid).fd_map[fd].off = off + 1
        return r

    @model.methodwrap(fd=SFdNum, off=SOffset, pid=SPid, internal_time=STime)
    def pread(self, fd, off, pid, internal_time):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        if self.getproc(pid).fd_map[fd].ispipe:
            return {'r': -1, 'errno': errno.ESPIPE}
        return self.iread(self.getproc(pid).fd_map[fd].inum, off, internal_time)

    def iwrite(self, inum, off, databyte, time):
        simsym.assume(off >= 0)
        ## Avoid overly-long files.  fs-test.py caps file size at 16 units.
        simsym.assume(off < 10)
        ## XXX Handle sparse files?
        simsym.assume(off <= self.i_map[inum].data._len)

        if off == self.i_map[inum].data._len:
            self.i_map[inum].data.append(databyte)
        else:
            self.i_map[inum].data[off] = databyte
        if time is not None:
            simsym.assume(time > self.i_map[inum].mtime)
            simsym.assume(time > self.i_map[inum].ctime)
            self.i_map[inum].mtime = time
            self.i_map[inum].ctime = time
        return {'r': 1}

    @model.methodwrap(fd=SFdNum, databyte=SDataByte, pid=SPid, internal_time=STime)
    def write(self, fd, databyte, pid, internal_time):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        if self.getproc(pid).fd_map[fd].ispipe:
            if not self.getproc(pid).fd_map[fd].pipewriter:
                return {'r': -1, 'errno': errno.EBADF}
            pipe = self.pipes[self.getproc(pid).fd_map[fd].pipeid]
            ## TODO: return EPIPE if no more readers
            simsym.assume(pipe.nread < pipe.data.len())
            simsym.assume(pipe.nread >= 0)
            pipe.data.append(databyte)
            return {'r': 1}
        off = self.getproc(pid).fd_map[fd].off
        self.getproc(pid).fd_map[fd].off = off + 1
        return self.iwrite(self.getproc(pid).fd_map[fd].inum, off, databyte, internal_time)

    @model.methodwrap(fd=SFdNum, off=SOffset, databyte=SDataByte, pid=SPid, internal_time=STime)
    def pwrite(self, fd, off, databyte, pid, internal_time):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        if self.getproc(pid).fd_map[fd].ispipe:
            return {'r': -1, 'errno': errno.ESPIPE}
        return self.iwrite(self.getproc(pid).fd_map[fd].inum, off, databyte, internal_time)

    def istat(self, inum):
        len = self.i_map[inum].data._len
        nlink = self.i_map[inum].nlink
        atime = self.i_map[inum].atime
        mtime = self.i_map[inum].mtime
        ctime = self.i_map[inum].ctime
        return {'r': 0, 'st.st_ino': inum, 'st.st_size': len,
                'st.st_nlink': nlink, 'st.st_atime': atime,
                'st.st_mtime': mtime, 'st.st_ctime': ctime}

    @model.methodwrap(pn=SPathname)
    def stat(self, pn):
        _, dirmap, pnlast = self.nameiparent(pn)
        if not dirmap.contains(pnlast):
            return {'r': -1, 'errno': errno.ENOENT}
        return self.istat(dirmap[pnlast])

    @model.methodwrap(fd=SFdNum, pid=SPid)
    def fstat(self, fd, pid):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        if self.getproc(pid).fd_map[fd].ispipe:
            return {'r': 0, '!!S_ISFIFO(st.st_mode)': 1}
        return self.istat(self.getproc(pid).fd_map[fd].inum)

    @model.methodwrap(fd=SFdNum, pid=SPid)
    def close(self, fd, pid):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        del self.getproc(pid).fd_map[fd]
        return {'r': 0}

    @model.methodwrap(fd=SFdNum, off=SOffset,
                      whence_set=simsym.SBool,
                      whence_cur=simsym.SBool,
                      whence_end=simsym.SBool,
                      pid=SPid)
    def lseek(self, fd, off, whence_set, whence_cur, whence_end, pid):
        self.add_selfpid(pid)
        if not self.getproc(pid).fd_map.contains(fd):
            return {'r': -1, 'errno': errno.EBADF}
        fdm = self.getproc(pid).fd_map[fd]
        if fdm.ispipe:
            return {'r': -1, 'errno': errno.ESPIPE}
        if whence_set:
            new_off = off
        elif whence_cur:
            new_off = fdm.off + off
        elif whence_end:
            new_off = self.i_map[fdm.inum].data._len + off
        else:
            return {'r': -1, 'errno': errno.EINVAL}
        if new_off < 0:
            return {'r': -1, 'errno': errno.EINVAL}
        fdm.off = new_off
        return {'r': fdm.off}

    @model.methodwrap(anon=simsym.SBool,
                      writable=simsym.SBool,
                      fixed=simsym.SBool,
                      va=SVa,
                      fd=SFdNum,
                      off=SOffset,
                      pid=SPid,
                      internal_freeva=SVa)
    def mmap(self, anon, writable, fixed, va, fd, off, pid, internal_freeva):
        ## TODO: MAP_SHARED/MAP_PRIVATE for files
        ##       -> how to model delayed file read?
        ## TODO: MAP_SHARED/MAP_PRIVATE for anon (with fork)
        ## TODO: zeroing anon memory
        self.add_selfpid(pid)
        myproc = self.getproc(pid)
        if not fixed:
            va = internal_freeva
            simsym.assume(simsym.symnot(myproc.va_map.contains(va)))
        if not anon:
            if not myproc.fd_map.contains(fd):
                return {'r': -1, 'errno': errno.EBADF}
            if myproc.fd_map[fd].ispipe:
                return {'r': -1, 'errno': errno.EACCES}
        vma = myproc.va_map.create(va)
        vma.anon = anon
        vma.writable = writable
        if anon:
            vma.anondata = SDataByte.var()
        else:
            vma.off = off
            vma.inum = myproc.fd_map[fd].inum
        # This has to be well-typed, so we use a different variable to
        # represent VAs.
        return {'r:va': va}

    @model.methodwrap(va=SVa, pid=SPid)
    def munmap(self, va, pid):
        self.add_selfpid(pid)
        del self.getproc(pid).va_map[va]
        return {'r': 0}

    @model.methodwrap(va=SVa, writable=simsym.SBool, pid=SPid)
    def mprotect(self, va, writable, pid):
        self.add_selfpid(pid)
        myproc = self.getproc(pid)
        if not myproc.va_map.contains(va):
            return {'r': -1, 'errno': errno.ENOMEM}
        myproc.va_map[va].writable = writable
        return {'r': 0}

    @model.methodwrap(va=SVa, pid=SPid, internal_time=STime)
    def memread(self, va, pid, internal_time):
        self.add_selfpid(pid)
        myproc = self.getproc(pid)
        if not myproc.va_map.contains(va):
            return {'r': -1, 'signal': signal.SIGSEGV}
        if myproc.va_map[va].anon:
            return {'r:data': myproc.va_map[va].anondata, 'signal': 0}
        ## TODO: memory-mapped reads don't bump atime?
        internal_time = None
        res = self.iread(myproc.va_map[va].inum, myproc.va_map[va].off * 4096, internal_time)
        if res['r'] == 0:
            # This means there was no page here
            return {'r': -1, 'signal': signal.SIGBUS}
        elif res['r'] == 1:
            return {'r:data': res['data'], 'signal': 0}
        else:
            raise RuntimeError('Unexpected result from iread: %r' % res)

    @model.methodwrap(va=SVa, databyte=SDataByte, pid=SPid, internal_time=STime)
    def memwrite(self, va, databyte, pid, internal_time):
        self.add_selfpid(pid)
        myproc = self.getproc(pid)
        if not myproc.va_map.contains(va):
            return {'r': -1, 'signal': signal.SIGSEGV}
        if not myproc.va_map[va].writable:
            return {'r': -1, 'signal': signal.SIGSEGV}
        if myproc.va_map[va].anon:
            myproc.va_map[va].anondata = databyte
            return {'r': 0, 'signal': 0}
        vma = myproc.va_map[va]
        if vma.off >= self.i_map[vma.inum].data._len:
            return {'r': -1, 'signal': signal.SIGBUS}
        ## TODO: memory-mapped writes don't bump mtime/ctime?
        internal_time = None
        res = self.iwrite(myproc.va_map[va].inum, myproc.va_map[va].off * 4096,
                          databyte, internal_time)
        if res['r'] == 1:
            return {'r': 0, 'signal': 0}
        else:
            raise RuntimeError('Unexpected result from iwrite: %r' % res)

model_class = Fs
model_functions = [
    Fs.open,
    Fs.pipe,
    Fs.pread,
    Fs.pwrite,
    Fs.read,
    Fs.write,
    Fs.unlink,
    Fs.link,
    Fs.rename,
    Fs.stat,
    Fs.fstat,
    Fs.close,
    Fs.lseek,
    Fs.mmap,
    Fs.munmap,
    Fs.mprotect,
    Fs.memread,
    Fs.memwrite,
]
model_testgen = fs_testgen.FsTestGenerator
