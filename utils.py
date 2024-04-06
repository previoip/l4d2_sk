import os
import logging
import tempfile
import functools
import re
import json
import time
import subprocess
from shutil import rmtree, copy2
from requests import Session, Response, ConnectionError as RequestsExceptionConnectionError
from urllib.parse import urlparse
from collections import namedtuple
from io import IOBase, BytesIO
from math import isclose
from zipfile import ZipFile

class logging_utils:
  fmt_default = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
  fmt_simple = logging.Formatter('{name:<12} [{levelname:<7}]: {message}', style='{')

  @classmethod
  def init_logger(cls, name: str, level=logging.DEBUG, do_stream_file=True, do_stream_stdout=True):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    if do_stream_stdout:
      logger_h = logging.StreamHandler()
      logger_h.setFormatter(cls.fmt_simple)
      logger.addHandler(logger_h)
    if do_stream_file:
      os.makedirs('./log', exist_ok=True)
      logger_h = logging.FileHandler(os.path.join('./log', name + '.log'))
      logger_h.setFormatter(cls.fmt_default)
      logger.addHandler(logger_h)
    return logger


file_utils_logger = logging_utils.init_logger('file_utils')

class file_utils:

  @staticmethod
  def split_file_name_format(s):
    s = str(s)
    ss = s.split('.')
    stub = ''
    if len(ss) == 2:
      s, stub = ss
    elif len(ss) > 2:
      s, stub = '.'.join(ss[:-1]), ss[-1]
    return s, stub

  @classmethod
  def normalize(cls, s):
    import unicodedata
    s = str(s)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s, stub = cls.split_file_name_format(s)
    # s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[-\s]+', '_', s).strip('_')
    if stub:
      s = s + '.' + stub
    return s

  @classmethod
  def mkdtemp(cls):
    tmpdir = tempfile.mkdtemp()
    file_utils_logger.debug('created tempdir: {}'.format(tmpdir))
    def destructor(d):
      if cls.isdir(tmpdir):
        rmtree(d)
        file_utils_logger.debug('destroyed tempdir: {}'.format(tmpdir))
    destructor_f = functools.partial(destructor, tmpdir)
    return tmpdir, destructor_f

  @staticmethod
  def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

  @staticmethod
  def isdir(path):
    return os.path.exists(path) and os.path.isdir(path)

  @staticmethod
  def isfile(path):
    return os.path.exists(path) and os.path.isfile(path)

  @staticmethod
  def path_join(*paths):
    return os.path.join(*paths)

  @staticmethod
  def relpath(path, start):
    return os.path.relpath(path, start)

  @staticmethod
  def rmtree_d(path):
    for root, dirs, files in os.walk(path):
      for f in files:
        os.unlink(os.path.join(root, f))
      for d in dirs:
        rmtree(os.path.join(root, d))

  @classmethod
  def copy2(cls, src, dst):
    cls.ensure_dir(os.path.dirname(dst))
    copy2(src, dst)


  @classmethod
  def copy2_r(cls, src, dst):
    for root, dirs, files, in os.walk(src):
      rel_dest = os.path.join(dst, os.path.relpath(root, src))
      for d in dirs:
        dst_dir = os.path.join(rel_dest, d)
        print('> {}'.format(dst_dir))
        cls.ensure_dir(dst_dir)
      for f in files:
        dst_file = os.path.join(rel_dest, f)
        print('> {}'.format(dst_file))
        copy2(os.path.join(root, f), dst_file)

  @classmethod
  def extract_zip(cls, path, dst, tmpdir=None):
    if tmpdir is None:
      zip_tempdir, d_zip_tempdir = cls.mkdtemp()
    else:
      d_zip_tempdir = lambda: None
      zip_tempdir = tmpdir
    with ZipFile(path) as zh:
      zh.extractall(zip_tempdir)
      cls.copy2_r(zip_tempdir, dst)
    if tmpdir is None:
      d_zip_tempdir()
    else:
      cls.rmtree_d(zip_tempdir)


http_utils_logger = logging_utils.init_logger('http_utils')

class http_utils:

  s_file_info = namedtuple("FileInfo", field_names=['file_name', 'file_type',  'file_size', 'content_disposition', 'content_type'])

  @staticmethod
  def head(session: Session, url):
    try:
      resp = session.head(url)
      if not resp.status_code == 200:
        raise RequestsExceptionConnectionError('connection cannot be established: {} {}'.format(resp.status_code, resp.reason))
    except Exception as e:
      http_utils_logger.error(e)
      return None
    return resp

  @staticmethod
  def get(session: Session, url, stream=False):
    try:
      resp = session.get(url, stream=stream)
      if not resp.status_code == 200:
        raise RequestsExceptionConnectionError('connection cannot be established: {} {}'.format(resp.status_code, resp.reason))
    except Exception as e:
      http_utils_logger.error(e)
      return None
    return resp

  @staticmethod
  def parse_url_filename(url):
    try:
      parsed_url = urlparse(url)
    except ValueError:
      return None
    path = parsed_url.path
    path_split = os.path.split(path)
    leaf = path_split[-1]
    return file_utils.normalize(leaf)

  @classmethod
  def parse_file_info(cls, headers: "CaseInsensitiveDict", url):
    content_disposition = headers.get('content-disposition')
    if content_disposition is None:
      file_name = cls.parse_url_filename(url)
    else:
      file_name = re.findall(r'filename=(.+)', content_disposition)[0].strip('"')
    file_name = file_utils.normalize(file_name)
    _, file_type = file_utils.split_file_name_format(file_name)
    content_length = headers.get('content-length')
    if content_length is None:
      content_length = -1
    else:
      content_length = int(content_length)
    content_type = headers.get('content-type', '')
    return cls.s_file_info(file_name, file_type, content_length, content_disposition, content_type)

  @classmethod
  def request_file_info(cls, session: Session, url):
    resp = cls.head(session, url)
    if resp is None:
      return None
    return cls.parse_file_info(resp.headers, url)

  @classmethod
  def get_stream_to_io(cls, session, url, io_object: IOBase):
    resp = cls.get(session, url, stream=True)

    if resp is None:
      return False, None

    t0 = time.time()
    tn = t0

    info = cls.parse_file_info(resp.headers, url)
    tl = 0.0
    dl = 0.0
    total_l = info.file_size

    try:
      for b in resp.iter_content():
        lb = len(b)
        dl += lb
        tl += lb
        io_object.write(b)
        dt = time.time() - tn
        if total_l > 0 and dt > 5:
          frac = tl/total_l
          print('downloading: {:>7.02f}% {:>10.02f}kb/s'.format(frac*100, dl/dt/1000))
          tn = time.time()
          dl = 0
      print('done downloading')

    except Exception as e:
      http_utils_logger.error(e)
      print('error occurred while downloading: {}'.format(e))
      return False, info

    return isclose(tl, total_l), info

  @classmethod
  def download_file(cls, session, url, export_dir, buf:IOBase=None):
    if buf is None:
      buf = BytesIO()

    info = cls.request_file_info(session, url)
    if info is None:
      return False, None, None

    export_path = file_utils.path_join(export_dir, info.file_name)

    if file_utils.isfile(export_path):
      file_utils_logger.info('file already exists: {}'.format(export_path))
      return True, export_path, info

    status, _ = cls.get_stream_to_io(session, url, buf)
    if status:
      buf.seek(0)
      file_utils.ensure_dir(export_dir)
      with open(export_path, 'wb') as fh:
        fh.write(buf.read())
    return status, export_path, info


steamapp_logger = logging_utils.init_logger('steamapp_man')

class SteamAppManager:

  steamcmd_file_host = 'https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip'
  steamcmd_dir = '/steamcmd'
  steamcmd_file_name = 'steamcmd.exe'

  def __init__(self, appid='', ds_appid='', ds_install_dir='', ds_file_path=''):
    self.appid = appid
    self.ds_appid = ds_appid
    self.ds_install_dir = ds_install_dir
    self.ds_file_path = ds_file_path
    self._userid = ''
    self._passwd = ''
    self.configure_auth('', '')

  def configure(self, appid, ds_appid, ds_install_dir, ds_file_path):
    self.appid = appid
    self.ds_appid = ds_appid
    self.ds_install_dir = ds_install_dir
    self.ds_file_path = ds_file_path

  def configure_auth(self, userid, passwd):
    self._userid = userid if userid else 'anonymous'
    self._passwd = passwd

  @classmethod
  def get_steamcmd_path(cls):
    return os.path.join(cls.steamcmd_dir, cls.steamcmd_file_name)

  @classmethod
  def is_steamcmd_installed(cls):
    return file_utils.isfile(cls.get_steamcmd_path())

  def get_app_path(self, *tails):
    return os.path.join(self.steamcmd_dir, self.ds_install_dir, *tails)

  @classmethod
  def download_steamcmd(cls, session):
    if not cls.is_steamcmd_installed():
      steamapp_logger.info('downloading steamcmd')
      status, path, info = http_utils.download_file(session, cls.steamcmd_file_host, cls.steamcmd_dir)
      if status:
        file_utils.extract_zip(path, cls.steamcmd_dir)
      steamapp_logger.info('extracted: {}'.format(cls.get_steamcmd_path()))
    else:
      steamapp_logger.info('stamcmd already exists: {}'.format(cls.get_steamcmd_path()))

  def prepare_args_update_app(self, validate=False):
    args = [
      self.get_steamcmd_path(),
      '+force_install_dir', self.ds_install_dir,
      '+login', 'anonymous',
      '+app_update', self.ds_appid
    ]
    if validate:
      args += ['+validate']
    return args + ['+quit']

  def prepare_args_workshop_download_item(self, workshop_item_id):
    args = [
      self.get_steamcmd_path(),
      '+force_install_dir', self.ds_install_dir,
      '+login', str(self._userid), str(self._passwd),
      '+workshop_download_item', self.appid, str(workshop_item_id)
    ]
    return args + ['+quit']

  def update_app(self):
    return subprocess.call(self.prepare_args_update_app(validate=False))

  def workshop_download_item(self, workshop_item_id):
    retcode = subprocess.call(self.prepare_args_workshop_download_item(workshop_item_id=workshop_item_id))
    return retcode