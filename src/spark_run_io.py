"""Small local/HDFS path adapter; Hadoop handles remote writes and streaming hashes."""
from pathlib import Path
from urllib.parse import urlsplit

class SparkPath:
    def __init__(self, value, spark=None):
        value = str(value)
        scheme = urlsplit(value).scheme
        if scheme not in ('', 'hdfs'): raise ValueError('Only local paths and hdfs:// URIs are supported')
        self.remote = scheme == 'hdfs'
        self.value = value.rstrip('/') if self.remote else str(Path(value).resolve())
        self.spark = spark
    def __str__(self): return self.value
    @property
    def name(self): return self.value.rsplit('/', 1)[-1]
    def __truediv__(self, child): return SparkPath(self.value+'/'+str(child), self.spark)
    def bind(self, spark): self.spark = spark
    def _handle(self):
        if self.spark is None: raise RuntimeError('Spark session not bound')
        path = self.spark._jvm.org.apache.hadoop.fs.Path(self.value)
        return path, path.getFileSystem(self.spark._jsc.hadoopConfiguration())
    def mkdir_exclusive(self):
        path, fs = self._handle()
        if fs.exists(path): raise FileExistsError(self.value)
        if not fs.mkdirs(path): raise OSError('Cannot create '+self.value)
    def write_text(self, text):
        if not self.remote:
            Path(self.value).write_text(text, encoding='utf-8'); return
        path, fs = self._handle()
        stream = fs.create(path, False)
        try: stream.write(bytearray(text.encode('utf-8')))
        finally: stream.close()
    def sha256(self):
        if not self.remote:
            from prepare_forecast_inputs import sha256_file
            return sha256_file(Path(self.value))
        path, fs = self._handle()
        stream = fs.open(path)
        try: return self.spark._jvm.org.apache.commons.codec.digest.DigestUtils.sha256Hex(stream)
        finally: stream.close()
