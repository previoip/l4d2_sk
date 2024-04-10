import os
import logging
import tempfile
import functools
import re
import json
import time
import subprocess
import tarfile
import filecmp
from shutil import rmtree, copy
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
    logger.info('')
    logger.info('='*48)
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
    s = s.replace('\'', '')
    s = s.replace('"', '')
    if s.lower().startswith('utf-8\'\'') or s.lower().startswith('utf_8\'\''):
      s = s[5:]
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

  @classmethod
  def ensure_dir(cls, path):
    if not cls.isdir(path):
      os.makedirs(path, exist_ok=True)
      return path
    return None

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
    copy(src, dst)

  @classmethod
  def copy2_r(cls, src, dst):
    for root, dirs, files, in os.walk(src):
      rel_dest = os.path.join(dst, os.path.relpath(root, src))
      for d in dirs:
        dst_dir = os.path.join(rel_dest, d)
        d_dst = cls.ensure_dir(dst_dir)
        if not d_dst is None:
          print(') {}'.format(dst_dir))
      for f in files:
        dst_file = os.path.join(rel_dest, f)
        d_dst = cls.copy2(os.path.join(root, f), dst_file)
        if not d_dst is None:
          print('> {}'.format(d_dst))

  @classmethod
  def validate_r(cls, src, dst):
    file_utils_logger.info('validating directory: {} -> {}'.format(src, dst))
    for root, dirs, files, in os.walk(src):
      rel_dest = os.path.join(dst, os.path.relpath(root, src))
      for f in files:
        dst_file = os.path.join(rel_dest, f)
        res = filecmp.cmp(os.path.join(root, f), dst_file)
        if not res: 
          if file_utils.isfile(dst_file):
            file_utils_logger.error('there is a problem copying file: {}'.format(dst_file))
          elif not file_utils.isfile(os.path.join(root, f)):
            file_utils_logger.error('src file missing')


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
    # cls.validate_r(zip_tempdir, dst)
    if tmpdir is None:
      d_zip_tempdir()
    else:
      cls.rmtree_d(zip_tempdir)

  @classmethod
  def extract_tar_xz(cls, path, dst, tmpdir=None):
    if tmpdir is None:
      tar_tempdir, d_tar_tempdir = cls.mkdtemp()
    else:
      d_tar_tempdir = lambda: None
      tar_tempdir = tmpdir
    with tarfile.open(path, mode='r') as th:
      th.extractall(tar_tempdir)
      cls.copy2_r(tar_tempdir, dst)
    if tmpdir is None:
      d_tar_tempdir()
    else:
      cls.rmtree_d(zip_tempdir)


http_utils_logger = logging_utils.init_logger('http_utils')

class http_utils:

  cache_dir = './cache'
  cache_index_path = './cache/_index.dat'
  cache_dict = None
  s_file_info = namedtuple("FileInfo", field_names=['file_name', 'file_type',  'file_size', 'content_disposition', 'content_type'])

  @classmethod
  def http_request(cls, method, session: Session, url, stream=False, max_depth=10, data=None):
    http_utils_logger.info('{} request: {}'.format(method, url))
    resp = session.request(method, url, stream=stream, data=data)
    if resp.status_code == 200:
      return resp
    elif resp.status_code == 301 or resp.status_code == 302:
      for i in range(max_depth):
        new_url = resp.headers.get('location')
        if new_url is None:
          new_url = resp.url
        if i > max_depth//2:
          stream = False
        http_utils_logger.info('redirecting {} request (stream:{}): {}'.format(method, stream, url))
        new_resp = session.request(method, new_url, stream=stream, allow_redirects=False, data=data)
        if new_resp.status_code == 200:
          return new_resp
        elif resp.status_code == 301 or resp.status_code == 302:
          resp = new_resp
        else:
          raise RequestsExceptionConnectionError('redirected connection cannot be established: {} {}'.format(new_resp.status_code, new_resp.reason))
    else:
      raise RequestsExceptionConnectionError('connection cannot be established: {} {}'.format(resp.status_code, resp.reason))

  @classmethod
  def head(cls, session: Session, url):
    try:
      resp = cls.http_request('HEAD', session, url)
    except Exception as e:
      http_utils_logger.error(e)
      return None
    return resp

  @classmethod
  def get(cls, session: Session, url, stream=False):
    try:
      resp = cls.http_request('GET', session, url, stream=True)
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
    elif not re.search(r'filename=(.+)', content_disposition) is None:
      file_name = re.findall(r'filename=(.+)', content_disposition)[0].strip('"')
    elif not re.search(r'filename\*=(.+)', content_disposition) is None:
      file_name = re.findall(r'filename\*=(.+)', content_disposition)[0].strip('"')
    file_name = file_utils.normalize(file_name)
    http_utils_logger.info('found filename: {}'.format(file_name))
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

    info = cls.parse_file_info(resp.headers, url)
    t0 = time.time()
    tn = t0
    tl = 0.0
    dl = 0.0
    total_l = info.file_size
    print(info)
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
      if not isclose(0, total_l) and not isclose(tl, total_l):
        raise RequestsExceptionConnectionError('downloaded file size does not match with content-length')
      print('done downloading')
    except Exception as e:
      http_utils_logger.error(e)
      print('error occurred while downloading: {}'.format(e))
      return False, info

    return True, info

  @classmethod
  def download_file(cls, session, url, export_dir, buf:IOBase=None, use_cache=True):
    if buf is None:
      buf = BytesIO()

    if use_cache and cls._cache_has_key(url):
      cached_path, cached_info = cls._cache_get(url)
      http_utils_logger.info('using cached file: {}'.format(cached_path))
      cached_export_path = file_utils.path_join(export_dir, cached_info.file_name)
      if file_utils.isfile(cached_export_path):
        file_utils_logger.info('file already exists (cached): {}'.format(cached_export_path))
        return True, cached_export_path, cached_info
      with open(cached_path, 'rb') as fh:
        buf.write(fh.read())
        return True, cached_export_path, cached_info

    info = cls.request_file_info(session, url)
    if info is None:
      return False, None, None

    export_path = file_utils.path_join(export_dir, info.file_name)

    if use_cache and file_utils.isfile(export_path):
      file_utils_logger.info('file already exists: {}'.format(export_path))
      return True, export_path, info

    status, _ = cls.get_stream_to_io(session, url, buf)
    if status:
      file_utils.ensure_dir(export_dir)
      with open(export_path, 'wb') as fh:
        buf.seek(0)
        fh.write(buf.read())
      file_utils.ensure_dir(cls.cache_dir)

    if status and use_cache: # and not cls._cache_has_key(url)
      cache_path = file_utils.path_join(cls.cache_dir, info.file_name)
      with open(cache_path, 'wb') as fh:
        buf.seek(0)
        fh.write(buf.read())
      cls._cache_add(url, cache_path, info)

    return status, export_path, info

  @classmethod
  def _cache_is_index_loaded(cls):
    return not cls.cache_dict is None

  @classmethod
  def _cache_has_key(cls, url):
    return cls._cache_is_index_loaded() and not cls.cache_dict.get(url) is None

  @classmethod
  def _cache_ensure(cls):
    if not cls._cache_is_index_loaded():
      cls.cache_dict = dict()

  @classmethod
  def _cache_load(cls):
    if cls._cache_is_index_loaded():
      return
    cls._cache_ensure()
    file_utils.ensure_dir(cls.cache_dir)
    if file_utils.isfile(cls.cache_index_path):
      with open(cls.cache_index_path, 'r') as fh:
        for line in fh.readlines():
          k, v = line.split('|')
          v = json.loads(v)
          cls.cache_dict.update({k:v})

  @classmethod
  def _cache_save(cls):
    file_utils.ensure_dir(cls.cache_dir)
    if not cls.cache_dict is None:
      with open(cls.cache_index_path, 'w') as fh:
        fh.writelines(map(lambda x: '{}|{}\n'.format(x[0], json.dumps(x[1])), cls.cache_dict.items()))

  @classmethod
  def _cache_add(cls, url, cached_file_path, info):
    cls._cache_ensure()
    cls.cache_dict.update({url: [
      cached_file_path,
      [
        info.file_name,
        info.file_type,
        info.file_size,
        info.content_disposition,
        info.content_type
      ]
    ]})

  @classmethod
  def _cache_get(cls, url):
    cached_file_path, info = cls.cache_dict.get(url)
    return cached_file_path, cls.s_file_info(*info)

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
  def get_steamcmd_path(cls, *tails):
    return os.path.join(cls.steamcmd_dir, cls.steamcmd_file_name, *tails)

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

  def update_app(self, validate=False):
    return subprocess.call(self.prepare_args_update_app(validate=validate))

  def workshop_download_item(self, workshop_item_id):
    retcode = subprocess.call(self.prepare_args_workshop_download_item(workshop_item_id=workshop_item_id))
    return retcode

  def workshop_download_item_extern(self, session: Session, workshop_item_id):
    steamapp_logger.info('retrieving workshop info: {}'.format(workshop_item_id))
    home = 'https://steamworkshopdownloader.io/'
    db_hostname = 'https://db.steamworkshopdownloader.io'
    db_api_path = 'prod/api/details/file'

    data = bytes(f'[{workshop_item_id}]', 'utf8')
    try:
      db_resp = http_utils.http_request('POST', session=session, url='{}/{}'.format(db_hostname, db_api_path), data=data)
    except Exception as e:
      http_utils_logger.error(e)
      return

    workshop_db_res = json.loads(db_resp.content)
    for workshop_ent in workshop_db_res:
    
      result = workshop_ent.get('result')
      file_url = workshop_ent.get('file_url')
      preview_url = workshop_ent.get('preview_url')
      file_name = workshop_ent.get('filename')
      is_collection = workshop_ent.get('show_subscribe_all', False)
      is_collection = is_collection and not workshop_ent.get('can_subscribe', False)
      
      steamapp_logger.info('downloading: {}'.format(file_name))

      if result: 
        if is_collection:
          steamapp_logger.info('workshop collection found instead, processing children')
          for workshop_child_ent in workshop_ent.get('children'):
            workshop_child_id = workshop_child_ent.get('publishedfileid')
            if not workshop_child_id is None:
              self.workshop_download_item_extern(session, workshop_child_id)
        else:
          if not file_url is None:
            export_dir = self.get_app_path('left4dead2/addons')
            # export_file = file_utils.path_join(export_dir, file_name)
            file_utils.ensure_dir(export_dir)
            fd, tmp_file_name = tempfile.mkstemp()
            with os.fdopen(fd, 'wb') as fh:
              try:
                http_utils.download_file(session, file_url, export_dir, fh)
              except Exception as e:
                http_utils_logger.error(e)

          if not preview_url is None:
            export_dir = self.get_app_path('left4dead2/addons')
            file_utils.ensure_dir(export_dir)
            fd, tmp_file_name = tempfile.mkstemp()
            with os.fdopen(fd, 'wb') as fh:
              try:
                http_utils.download_file(session, preview_url, export_dir, fh)
              except Exception as e:
                http_utils_logger.error(e)
      else:
        steamapp_logger.warning('cannot retrieve workshop info: {}'.format(workshop_item_id))