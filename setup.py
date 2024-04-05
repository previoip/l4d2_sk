import logging

DEBUG_IGNORE_CONN = True

logger = logging.getLogger('setup')
def _logger_init():
  logger.setLevel(logging.DEBUG)
  logger.handlers.clear()
  logger_sh = logging.StreamHandler()
  logger_fh = logging.FileHandler('setup.log')
  logger_fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
  logger_sh.setFormatter(logger_fmt)
  logger_fh.setFormatter(logger_fmt)
  logger.addHandler(logger_sh)
  logger.addHandler(logger_fh)
  logger.info('='*48)
_logger_init()

import sys
import os
import re
import subprocess
import requests
import json
import tempfile
from shutil import rmtree, copy2
from io import BytesIO
from zipfile import ZipFile
from time import time

class config:
  platform = 'windows'
  steamcmd_file_host = 'https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip'
  steamcmd_dir = './steamcmd_temp'
  steamcmd_file_name = 'steamcmd.exe'
  appid = '550'
  ds_appid = '222860'
  ds_reldir = 'l4d2'
  ds_file_name = 'srcds.exe'
  cache_dir = './cache'
  userid = 'anonymous' if len(sys.argv) == 1 else sys.argv[1]
  passwd = '' if len(sys.argv) == 1 else sys.argv[1]

  @classmethod
  def parse_steamcmd_path(cls, tails=[]):
    return os.path.abspath(os.path.join(cls.steamcmd_dir, *tails))

  @classmethod
  def is_dir_exist(cls):
    return os.path.exists(cls.parse_steamcmd_path()) and os.path.isdir(cls.parse_steamcmd_path())

  @classmethod
  def is_steamcmd_exist(cls):
    steam_cmd_path = cls.parse_steamcmd_path([cls.steamcmd_file_name])
    return os.path.exists(steam_cmd_path) and \
      os.path.isfile(steam_cmd_path) and \
      os.access(steam_cmd_path, os.X_OK)

  @classmethod
  def make_steamcmd_dir(cls):
    os.makedirs(cls.steamcmd_dir, exist_ok=True)

  @classmethod
  def download_steamcmd(cls):
    if cls.is_steamcmd_exist():
      logger.info('steamcmd executable already exists')
      return
    try:
      resp = requests.get(cls.steamcmd_file_host)
      if resp.status_code == 200:
        buf = BytesIO(resp.content)
      else:
        raise requests.ConnectionError('cannot retrieve content from steamcmd file host.')
      if not config.is_dir_exist():
        config.make_steamcmd_dir()
      ziph = ZipFile(buf)
      ziph.extractall(cls.steamcmd_dir)
    except Exception as e:
      logger.error(e)
      return

  @classmethod
  def parse_ds_path(cls, tails=[]):
    return os.path.abspath(os.path.join(cls.steamcmd_dir, cls.ds_reldir, *tails))

  @classmethod
  def is_ds_exist(cls):
    ds_path = cls.parse_ds_path([cls.ds_file_name])
    return os.path.exists(ds_path) and \
      os.path.isfile(ds_path) and \
      os.access(ds_path, os.X_OK)

  @classmethod
  def update_app(cls):
    return subprocess.call([
      cls.parse_steamcmd_path([cls.steamcmd_file_name]),
      '+force_install_dir', cls.parse_ds_path(),
      '+login', 'anonymous',
      '+app_update', cls.ds_appid,
      '+quit'
    ])

  @classmethod
  def download_workshop(cls, workshop_id):
    return subprocess.call([
      cls.parse_steamcmd_path([cls.steamcmd_file_name]),
      '+force_install_dir', cls.parse_ds_path(),
      '+login', cls.userid, cls.passwd,
      '+workshop_download_item',
      cls.appid,
      str(workshop_id),
      '+quit'
    ])


def rmtree_d(path):
  for root, dirs, files in os.walk(path):
    for f in files:
        os.unlink(os.path.join(root, f))
    for d in dirs:
        rmtree(os.path.join(root, d))

def parse_response_header(resp: requests.Response):
  file_name = resp.headers.get('content-disposition')
  if file_name is None:
    file_name = str(url).split('/')[-1]
  else:
    file_name = re.findall(r'filename=(.+)', file_name)[0][1:-1]

  content_type = resp.headers.get('content-type')
  if not content_type is None:
    content_type = content_type.split('/')[-1]
    if not file_name.endswith(content_type):
      file_name += '.' + content_type

  content_length = resp.headers.get('content-length')
  if not content_length is None:
    content_length = int(content_length)

  return content_type, content_length, file_name


def http_check_connection(s: requests.Session, url):
  logger.info('checking connection: {}'.format(url))
  if DEBUG_IGNORE_CONN:
    logger.debug('connection check ignored')
    return True
  try:
    resp = s.head(url, timeout=10)
    res = resp.status_code == 200
    if not res:
      raise requests.ConnectionError('connection refused: {} {}'.format(resp.status_code, resp.reason))
    else:
      logger.info('connection established')
    return res
  except Exception as e:
    logger.warning('connection cannot be established: {}'.format(e))
    return False


def http_download_content(s: requests.Session, url, path='.', stream_chunk_size=4096):
  logger.info('downloading content: {}'.format(url))
  # conn = http_check_connection(s, url)
  # if not conn:
  #   return None, None, None, None

  try:
    resp_head = s.head(url, timeout=10)
  except Exception as e:
    logger.error(e)
    return None, None, None, None
    
  
  content_type, content_length, file_name = parse_response_header(resp_head)

  path = os.path.join(path, file_name)
  if os.path.exists(path) and os.path.isfile(path):
    logger.info('file already exist: {}'.format(path))
    return content_type, content_length, file_name, path

  try:
    resp = s.get(url, stream=True, timeout=10)
  except Exception as e:
    logger.error(e)
    return None, None, None, None

  buf = BytesIO()
  if content_length is None:
    logger.warning('content-length is null, trying flush content to buffer')
    try:
      buf.write(resp.content)
    except Exception as e:
      logger.error(e)
      return None, None, None, None
  else:
    l = 0
    ln = 0
    t0 = time()
    tn = time()
    try:
      for b in resp.iter_content(chunk_size=stream_chunk_size):
        lb = len(b)
        l += lb
        ln += lb
        buf.write(b)
        elapsed = time() - tn
        if elapsed > 3:
          print('downloading: {:>7.02f}% {:>10.02f}kb/s'.format(l*100/content_length, ln/elapsed/1000))
          tn = time()
          ln = 0
    except Exception as e:
      logger.error(e)
      del buf
      return None, None, None, None
    elapsed = time() - t0
    print('elapsed: {:.02f} seconds, average speed {:.02f} kb/s'.format(elapsed, content_length/elapsed/1000))
  with open(path, 'wb') as fh:
    buf.seek(0)
    fh.write(buf.read())
  return content_type, content_length, file_name, path


if __name__ == '__main__':

  logger.info('loading setup_config.json')
  with open('setup_config.json', 'r') as fh:
    setup_config = json.load(fh)

  config.platform = setup_config.get('config', {}).get('usePlatform', config.platform)
  config.appid = setup_config.get('config', {}).get('appid', config.appid)
  config.ds_appid = setup_config.get('config', {}).get('appidDedicatedServer', config.ds_appid)
  config.ds_reldir = setup_config.get('config', {}).get('forceInstallDir', config.ds_reldir)

  config.download_steamcmd()

  # logger.info('updating app from steamcmd')
  # config.update_app()
  # logger.info('done updating app')

  tempdir = tempfile.mkdtemp()
  os.makedirs(config.cache_dir, exist_ok=True)

  plugins = setup_config.get('metaPlugins', [])
  plugins.extend(setup_config.get('plugins', []))

  with requests.Session() as session:
    session.headers.update({
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36',
    })

    if (http_check_connection(session, 'https://forums.alliedmods.net') and \
      http_check_connection(session, 'https://sm.alliedmods.net')):

      for plugin in plugins:
        name = plugin.get('name')
        extract_path = plugin.get('extractPath')
        resources = plugin.get('resources')
        resources = filter(lambda x: bool(x.get('url')) and (x.get('platform') == "*" or x.get('platform') == config.platform), resources)

        logger.info('processing plugin: {}'.format(name))
        for resource in resources:

          url = resource.get('url')
          do_compile = resource.get('doCompile', False)
          compiler_path = resource.get('compilerLocation')
          compiler_params = resource.get('compilerParams', [])
          plugin_extract_path = resource.get('extractPath')

          if plugin_extract_path is None or plugin_extract_path == "":
            print('using', extract_path, compiler_path)
            plugin_extract_path = extract_path

          if url is None:
            logger.warning('missing resource url on: {}'.format(name))
            continue

          path = resource.get('fileName')
          if not path is None:
            path = os.path.join(config.cache_dir, path)
            if os.path.exists(path) and os.path.isfile(path):
              logger.info('file already downloaded: {}'.format(path))
            else:
              logger.info('file is not found, trying to download file: {}'.format(path))
              _, _, _, path = http_download_content(session, url, config.cache_dir)

          # if path is None:
          #   continue

          if plugin_extract_path is None:
            logger.warning('plugin extract path is None')
            continue

          logger.info('extracting files...')
          with ZipFile(path) as zh:

            files = zh.filelist
            zh.extractall(tempdir)
            for root, dirs, files in os.walk(tempdir):
              for d in dirs:
                tmppath = os.path.join(root, d)
                relpath = os.path.relpath(tmppath, tempdir)
                sdspath = config.parse_ds_path([plugin_extract_path, relpath])
                os.makedirs(sdspath, exist_ok=True)
              for f in files:
                tmppath = os.path.join(root, f)
                relpath = os.path.relpath(tmppath, tempdir)
                sdspath = config.parse_ds_path([plugin_extract_path, relpath])
                print('>', relpath)
                copy2(tmppath, sdspath)
                # if do_compile and not compiler_path is None:
                #   compiler_full_path = config.parse_ds_path([compiler_path])
                #   if (os.path.exists(compiler_full_path) and os.path.isfile(compiler_full_path)):
                #     for params in compiler_params:
                #       infile = params.get('in')
                #       outifile = params.get('out')
                #       if not infile is None:
                #         subprocess.call([compiler_full_path, infile])

          rmtree_d(tempdir)
          logger.info('done extracting files')


  logger.info('creating shell script')

  console_args_server_start = [
    os.path.relpath(config.parse_ds_path(['srcds.exe']), '.'),
    '-console',
    '+game left4dead2',
    '+ip 127.0.0.1',
    '+port 27020',
    # '+hostip dedicated_server_ip_addr',
    '+maxplayers 8',
    '+exec server.cfg',
    '+map c2m1_highway'
  ]

  with open('start.sh', 'w') as fh:
    fh.write(' '.join(console_args_server_start))


  # with open('setup_config_2.json', 'w') as fh:
  #   json.dump(setup_config, fh, indent=2)

  logger.info('copying server config')
  copy2('server.cfg', config.parse_ds_path(['left4dead2', 'cfg', 'server.cfg']))

  logger.info('downloading workshops')
  workshop_ids = setup_config.get('workshopIds', [])
  for rel_url, workshop_id in workshop_ids:
    config.download_workshop(workshop_id)

  dl_workshop_path = config.parse_ds_path(['steamapps', 'workshop', 'content', config.appid])
  tgt_workshop_path = config.parse_ds_path(['left4dead2', 'addons'])

  for root, dirs, files in os.walk(dl_workshop_path):
    for f in files:
      tmppath = os.path.join(root, f)
      relpath = os.path.relpath(tmppath, tempdir)
      wkspath = config.parse_ds_path([tgt_workshop_path, f])
      print('>', wkspath)
      copy2(tmppath, wkspath)

  logger.info('trying start dedicated server')
  subprocess.call(console_args_server_start + ['-exit'])

  rmtree(tempdir)
  logger.info('finished')
