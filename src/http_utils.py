import typing as t
import os
import requests
import re
from io import IOBase
from urllib.parse import urlparse
from time import time, sleep
from collections import namedtuple, deque
from src.log import init_logger
from src.path_utils import extract_file_type, ensure_dir, isfile

logger = init_logger('http_utils', 'setup.log')
file_info_t = namedtuple("FileInfo", field_names=['file_name', 'file_type',  'file_size', 'content_disposition', 'content_type'])

class RetryableConnectionError(requests.exceptions.BaseHTTPError):
  pass

def new_session(request_headers=None) -> requests.Session:
  session = requests.Session()
  if request_headers:
    session.headers.update(request_headers)
  logger.info('instantiating new session.')
  return session

def url_basename(url) -> str:
  r = urlparse(url)
  return r.path.rstrip('/').split('/')[-1]

def http_request(session: requests.Session, method, url, max_retry=3, force_retry=False, max_depth=10, **kwargs) -> t.Optional[requests.Response]:
  logger.info('{} {}'.format(method, url))
  for i_retry in range(1, max_retry + 1):
    for i_depth in range(max_depth):
      try:
        resp = session.request(method, url, **kwargs)
        if resp.status_code == 301 or resp.status_code == 302:
          url = resp.headers.get('location')
          if url is None:
            url = resp.url
          logger.info('redirecting connection: {} {}'.format(i_depth, url))
          continue
        elif resp.status_code == 200:
          logger.info('{} ok'.format(method))
          return resp
        elif force_retry:
          raise RetryableConnectionError('{} {} forcing retry attempt'.format(resp.status_code, resp.reason))
        else:
          raise requests.ConnectionError('cannot establish connection: {} {}'.format(resp.status_code, resp.reason))
      except (requests.Timeout, RetryableConnectionError):
        logger.warning('retrying connection {}'.format(i_retry))
        sleep(2.5)
        break
      except Exception as e:
        logger.error('error occured: {}'.format(e))
        return None
  return None

def stream_to_buf(resp: requests.Response, buf: IOBase, chunk_size=4096, content_length=0, update_stdout_sec=5) -> bool:
  total_l = 0
  dl = 0
  dt = 0
  t0 = time()
  tn = t0
  speed = 0
  speed_avg = 0
  speed_rec = deque(list(), maxlen=5)

  if content_length == 0:
    content_length = int(resp.headers.get('content-length', 0))
  try:
    for b in resp.iter_content(chunk_size):
      buf.write(b)
      bl = len(b)
      total_l += bl
      dl += bl
      dt = time() - tn
      if content_length > 0 and dt > update_stdout_sec:
        ratio = total_l / content_length
        speed = dl/max(dt, 0.01)
        speed_rec.append(speed)
        speed_avg = sum(speed_rec) / len(speed_rec)
        est_s = (content_length - total_l) / speed_avg

        est_m, est_s = divmod(est_s, 60)
        est_h, est_m = divmod(est_m, 60)
        print('downloading: {:>7.02%} | {:>12.02f} kb/s | est {:>3d}:{:>02d}:{:>02d}.{:<02d}'.format(ratio, speed_avg/1000, int(est_h), int(est_m), int(est_s), min(int(est_s%1*100), 99)))
        dl = 0
        tn = time()
      elif dt > update_stdout_sec:
        speed = dl/max(dt, 1e-8)
        speed_rec.append(speed)
        speed_avg = sum(speed_rec) / len(speed_rec)
        dl = 0
        tn = time()
        print('downloading {:>7.02f}kb/s | {:g}kb'.format(speed_avg, total_l/1000))
  except Exception as e:
    logger.error('error ocurred during fetch stream: {}'.format(e))
    return False
  logger.info('download finished')
  return True

def parse_headers_file_name(headers):
  content_disposition = headers.get('content-disposition', '')
  file_name = ''
  if not re.search(r'filename=(.+)', content_disposition) is None:
    file_name = re.findall(r'filename=(.+)', content_disposition)[0].strip('"')
  elif not re.search(r'filename\*=(.+)', content_disposition) is None:
    file_name = re.findall(r'filename\*=(.+)', content_disposition)[0].strip('"')
    # RFC 6266 - attfn2231
    if file_name.lower().startswith('utf-8'):
      file_name = file_name[7:]
  return file_name.strip('\'\\".;)*\ ')

def parse_headers_content_length(headers):
  content_length = headers.get('content-length', '0')
  if content_length.isnumeric():
    content_length = int(content_length)
  else:
    content_length = 0
  return content_length

def parse_headers_content_type(headers):
  return headers.get('content-type', '')

def parse_file_info(headers, url):
  content_disposition = headers.get('content-disposition', '')
  file_name = parse_headers_file_name(headers)
  if not file_name:
    file_name = url_basename(url)
  _, _, file_type = extract_file_type(file_name)
  content_type = parse_headers_content_type(headers)
  content_length = parse_headers_content_length(headers)
  return file_info_t(file_name, file_type, content_length, content_disposition, content_type)

def download_file(session, url, dst_dir, file_name='', chunk_size=4096, head_err_max_retry=5):
  logger.info('retrieving file info: {}'.format(url))

  skip_get_request = False
  resp = http_request(session, 'HEAD', url, allow_redirects=False, force_retry=True)
  if resp is None:
    logger.warning('cannot retrieve HEAD, changing method to GET')
    resp = http_request(session, 'GET', url, allow_redirects=False, stream=True)
    if resp is None:
      logger.error('unable to retrieve GET request')
      return False, None, None
    skip_get_request = True

  ensure_dir(dst_dir)

  file_info = parse_file_info(resp.headers, url)
  dst_path = os.path.join(dst_dir, file_info.file_name)
  if isfile(dst_path):
    if os.path.getsize(dst_path) != file_info.file_size:
      logger.info('file size did not match, redownloading: {}'.format(dst_path))
      os.unlink(dst_path)
    else:
      logger.info('file already exists: {}'.format(dst_path))
      resp.close()
      return True, dst_path, file_info

  if not skip_get_request:
    resp = http_request(session, 'GET', url, stream=True, allow_redirects=False)

  if resp is None:
    logger.error('unable to retrieve GET request')
    return False, dst_path, file_info
  
  logger.info('downloading to {}'.format(dst_path))
  with open(dst_path, 'wb') as fh:
    status = stream_to_buf(resp, fh, content_length=file_info.file_size)
  resp.close()

  return status, dst_path, file_info