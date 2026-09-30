"""Seek within one original stored ZIP member through bounded parent ranges."""
import io
import struct
import zipfile


def member_identity(info):
    return {'name':info.filename,'offset':info.header_offset,'bytes':info.file_size,
            'compressed_bytes':info.compress_size,'crc32':info.CRC,'compression':info.compress_type}


class ZipMemberWindow(io.RawIOBase):
    def __init__(self,parent,name,expected=None):
        self.parent=parent
        with zipfile.ZipFile(parent) as archive:
            info=archive.getinfo(name)
            self.member=member_identity(info)
            if expected is not None and self.member!=expected:
                raise ValueError('Frozen nested ZIP member changed')
            if info.compress_type!=zipfile.ZIP_STORED or info.flag_bits&1 or info.file_size!=info.compress_size:
                raise ValueError('Nested ZIP requires an unencrypted stored outer member')
        parent.seek(info.header_offset);header=parent.read(30)
        if len(header)!=30:raise ValueError('Incomplete outer local header')
        fields=struct.unpack('<4s5H3I2H',header)
        if fields[0]!=b'PK\x03\x04' or fields[3]!=0 or fields[2]&1:
            raise ValueError('Outer local header is not a stored ZIP member')
        raw_name=parent.read(fields[-2]);parent.read(fields[-1])
        if raw_name.decode('utf8' if fields[2]&0x800 else 'cp437')!=name:
            raise ValueError('Outer local member name changed')
        self.start=parent.tell();self.length=info.file_size;self.position=0
        if self.start+self.length>parent.length:raise ValueError('Nested member exceeds its parent archive')

    @property
    def etag(self):return self.parent.etag
    @property
    def transferred(self):return self.parent.transferred
    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        if whence not in (0,1,2):raise ValueError('Invalid nested seek mode')
        position=offset+(0 if whence==0 else self.position if whence==1 else self.length)
        if not 0<=position<=self.length:raise ValueError('Seek outside nested archive')
        self.position=position;return position
    def read(self,size=-1):
        size=min(self.length-self.position,size if size>=0 else self.length-self.position)
        if not size:return b''
        self.parent.seek(self.start+self.position);raw=self.parent.read(size)
        if len(raw)!=size:raise ValueError('Truncated nested archive range')
        self.position+=len(raw);return raw
