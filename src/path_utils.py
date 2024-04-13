import os
import shutil
import tempfile
import functools
from collections import namedtuple
from src.log import init_logger
from zipfile import ZipFile
import tarfile

logger = init_logger('pathlib', 'setup.log')
extracted_file_type_t = namedtuple('ExtractedFileType', ['src', 'file_name', 'file_extension'])

def isdir(path):
  return os.path.exists(path) and os.path.isdir(path)

def isfile(path):
  return os.path.exists(path) and os.path.isfile(path)

def ensure_dir(path):
  if not isdir(path):
    os.makedirs(path)
    return path
  return None

def mkdtemp():
  temp_dir = tempfile.mkdtemp()
  logger.info('creating tempdir: {}'.format(temp_dir))
  def destructor(p):
    logger.info('destroying tempdir: {}'.format(p))
    return shutil.rmtree(p)
  return temp_dir, functools.partial(destructor, temp_dir)

def extract_file_type(s):
  splits = s.split('.')
  ext = ''
  file_name = s
  if len(splits) > 2 and splits[-2] == 'tar':
    ext = '.'.join(splits[-2:])
    file_name = '.'.join(splits[:-2])
  elif len(splits) > 1:
    ext = splits[-1]
    file_name = '.'.join(splits[:-1])
  s = '.'.join(splits)
  return extracted_file_type_t(s, file_name, ext)

def delete_file(path):
  return os.unlink(path)

def rmtree_d(path):
  for root, dirs, files in os.walk(path):
    for f in files:
      os.unlink(os.path.join(root, f))
    for d in dirs:
      shutil.rmtree(os.path.join(root, d))

def copy2(src, dst):
  ensure_dir(os.path.dirname(dst))
  return shutil.copy2(src, dst)

def copy2_r(src, dst):
  for root, dirs, files, in os.walk(src):
    rel_dest = os.path.join(dst, os.path.relpath(root, src))
    for d in dirs:
      dst_dir = os.path.join(rel_dest, d)
      d_dst = ensure_dir(dst_dir)
      if not d_dst is None:
        print(') {}'.format(dst_dir))
    for f in files:
      dst_file = os.path.join(rel_dest, f)
      d_dst = copy2(os.path.join(root, f), dst_file)
      if not d_dst is None:
        print('> {}'.format(d_dst))

def archive_extract_zip(path, dst, tmpdir=None):
  if tmpdir is None:
    zip_tempdir, d_zip_tempdir = mkdtemp()
  else:
    d_zip_tempdir = lambda: None
    zip_tempdir = tmpdir
  with ZipFile(path) as zh:
    zh.extractall(zip_tempdir)
    copy2_r(zip_tempdir, dst)
  if tmpdir is None:
    d_zip_tempdir()
  else:
    rmtree_d(zip_tempdir)

def archive_extract_tar(path, dst, tmpdir=None):
  if tmpdir is None:
    tar_tempdir, d_tar_tempdir = mkdtemp()
  else:
    d_tar_tempdir = lambda: None
    tar_tempdir = tmpdir
  with tarfile.open(path, mode='r') as th:
    th.extractall(tar_tempdir)
    copy2_r(tar_tempdir, dst)
  if tmpdir is None:
    d_tar_tempdir()
  else:
    rmtree_d(tar_tempdir)