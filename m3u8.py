#!/usr/bin/python3

#coding: utf-8
import sys
from sys import argv
from gevent import monkey
monkey.patch_all()
from gevent.pool import Pool
import gevent
import requests
from urllib.parse import urljoin
import os
import time
import getopt
import random
import string
from functools import partial
import re
from Crypto.Cipher import AES
import socks
import socket

class Downloader:
    def __init__(self, pool_size, retry=3, proxy_port=-1, referer=None):
        self.pool = Pool(pool_size)
        self.session = self._get_http_session(pool_size, pool_size, retry)
        self.headers = {
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0',
            'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="98", "Google Chrome";v="98"',
            'sec-ch-ua-mobile': '?0',
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36',
            'Accept': '*/*'
        }
        if referer is not None:
            self.headers['Referer'] = referer
        self.retry = retry
        self.dir = ''
        self.succed = {}
        self.failed = []
        self.ts_total = 0
        self.ts_finish = 0

        if proxy_port > 0:
            socks.set_default_proxy(socks.SOCKS5, '127.0.0.1', proxy_port)
            socket.socket = socks.socksocket


    def _get_http_session(self, pool_connections, pool_maxsize, max_retries):
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_connections, pool_maxsize=pool_maxsize, max_retries=max_retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    def _load_encryption_key(self, m3u8_content, m3u8_url):
        # 使用re正则得到key和视频地址
        jiami = re.findall('#EXT-X-KEY:(.*)\n', m3u8_content)
        if len(jiami) == 0:
            return None

        urls = re.findall('URI="(.*)"', jiami[0])
        if len(urls) == 0:
            return None

        key_url = urljoin(m3u8_url, urls[0])
        keycontent = requests.get(key_url, headers=self.headers, timeout=30).content

        # 得到解密方法，这里要导入第三方库  pycrypto
        # 这里有一个问题，安装pycrypto成功后，导入from Crypto.Cipher import AES报错
        # 找到使用python环境的文件夹，在Lib文件夹下有一个 site-packages 文件夹，里面是我们环境安装的包。
        # 找到一个crypto文件夹，打开可以看到 Cipher文件夹，此时我们将 crypto文件夹改为 Crypto 即可使用了
        return AES.new(keycontent, AES.MODE_CBC, keycontent)

    def run(self, m3u8_url, dir='', out_file='', start_time=0, end_time=-1, start_file=0, end_file=0, automerge=True):
        self.dir = dir
        if self.dir and not os.path.isdir(self.dir):
            os.makedirs(self.dir)
        if out_file == '':
            out_file = m3u8_url.split('/')[-1].split('?')[0].replace('.m3u8', '.ts')
        r = self.session.get(m3u8_url, headers=self.headers, timeout=30)
        if r.ok:
            # python3需要使用decode()把获取到的内容bytes r.content转换为str body
            body = r.content.decode()
            if body:
                if "#EXTM3U" not in body:
                    print("这不是一个m3u8的视频链接！")
                    return

                # 把每一行不以'#'开头的m3u8源文件加入ts_list中
                ts_list = [urljoin(m3u8_url, n.strip()) for n in body.split(
                    '\n') if n and not n.startswith("#")]
                if len(ts_list) <= 0:
                    print("m3u8文件为空！")
                    return

                if ts_list[0].endswith('.m3u8'):
                    self.run(ts_list[0], dir, out_file, start_time, end_time, start_file, end_file, automerge)
                    return

                decryptor = self._load_encryption_key(body, m3u8_url)
                if decryptor is not None:
                    print('[Decryption key loaded]')

                # 如果start_file和end_file不存在，则使用start_time和end_time来决定。
                if not (start_file or end_file):
                    # 如果start_time不存在，则默认为0
                    if not (start_time):
                        start_time = 0
                    # 如果end_time不存在，则默认为-1
                    if not (end_time):
                        end_time = -1
                    # 提取body中'#EXTINF:1234.56,'行中的浮点数，从':'后到','前，作为ts_list对应的ts_time
                    ts_time = [float(n[8:-1].split(',')[0]) for n in body.split('\n')
                               if n and n.startswith("#EXTINF:")]
                    i = 0
                    start_file = 0
                    # 对ts_time依次：
                    # 图解'['开始，']'结束，===文件，数字-索引：0======.1===[====.2======.3======.4====]====.5=======
                    # 应从1开始下载，到4结束。
                    for index in range(len(ts_time)):
                        # 计算增加此文件后总时长
                        i += ts_time[index]
                        # 如果增加后小于等于开始时长，那么应该从下一个文件开始，即index+1。
                        # {0======}.1===[====.2======.3======.4====]====.5=======
                        # 加了0文件时长后{}内时长小于等于开始时长'['，应从1开始，加了1后不会再触发此条件。
                        if i <= start_time:
                            start_file = index+1
                        # 如果增加后大于等于结束时长，且结束不为-1，那么应该从当前文件结束，即index。
                        # {0======.1===[====.2======.3======.4====]====}.5=======
                        # 加了4文件时长后{}内时长大于等于结束时长']'，应从4结束，但实际索引从5结束，即下载1[]5之间内容。
                        # 实际下载内容：0======.1 { ===[====.2======.3======.4====]====. } 5=======
                        if i >= end_time and end_time != -1:
                            end_file = index+1
                            break
                        # 显示start_file和end_file
                    print("[Start File]:\t", start_file)
                    print("[End File]:\t", end_file)
                # 没有设置end_file默认最后结束
                if not end_file:
                    end_file = len(ts_time)
                ts_list = ts_list[start_file:end_file]
                ts_list = list(zip(ts_list, [n for n in range(len(ts_list))]))
                if ts_list:
                    self.ts_total = len(ts_list)
                    print('[Total files]:'+str(self.ts_total))
                    if automerge:
                        g1 = gevent.spawn(self._join_file, out_file)
                    self._download(ts_list, decryptor)
                    if automerge:
                        g1.join()
        else:
            print(r.status_code)

    def _download(self, ts_list, decryptor):
        uniqueid = ''.join(random.sample(string.ascii_letters + string.digits, 8))
        dowork = partial(self._worker, uniqueid, decryptor)
        self.pool.map(dowork, ts_list)
        if self.failed:
            ts_list = self.failed
            self.failed = []
            self._download(ts_list, decryptor)

    def _worker(self, uniqueid, decryptor, ts_tuple):
        url = ts_tuple[0]
        index = ts_tuple[1]
        retry = self.retry
        #prefix = ''.join(random.sample(string.ascii_letters + string.digits, 8)) + '_'
        while retry:
            try:
                r = self.session.get(url, headers=self.headers, timeout=30)
                if r.ok:
                    original_file_name = url.split('/')[-1].split('?')[0]
                    (file_name, ext) = os.path.splitext(original_file_name)
                    file_name = file_name + '_' + uniqueid + ext
                    self.ts_finish += 1
                    content_length = r.headers['content-length'] if 'content-length' in r.headers else len(r.content)
                    print(original_file_name+'\t|\t' + str(content_length) + 'B\t|\t' + str(self.ts_finish)+'/'+str(self.ts_total))
                    with open(os.path.join(self.dir, file_name), 'wb') as f:
                        if decryptor is None:
                            f.write(r.content)
                        else:
                            f.write(decryptor.decrypt(r.content))
                    self.succed[index] = file_name
                    return
            except:
                retry -= 1
        print('[Fail]%s' % url)
        self.failed.append((url, index))

    def _join_file(self, out_file_name=''):
        index = 0
        outfile = None
        while index < self.ts_total:
            file_name = self.succed.get(index, '')
            if file_name:
                infile = open(os.path.join(self.dir, file_name), 'rb')
                if not outfile:
                    if out_file_name == '':
                        outfile = open(os.path.join(self.dir, file_name.split('.')[
                                       0]+'_all.'+file_name.split('.')[-1]), 'wb')
                    elif out_file_name == '-':
                        outfile = os.fdopen(sys.stdout.fileno(), 'wb', closefd=False)
                    else:
                        outfile = open(os.path.join(self.dir, out_file_name), 'wb')
                outfile.write(infile.read())
                infile.close()
                os.remove(os.path.join(self.dir, file_name))
                index += 1
            else:
                time.sleep(1)
        if outfile:
            outfile.close()


if __name__ == '__main__':
    if (len(sys.argv) == 1):
        # 未定义参数
        cm3u8url = input('m3u8地址:')
        cpath = input('下载路径(留空为当前路径):')
        proset = input('高级配置参数?(留空跳过):')
    else:
        if len(sys.argv) == 2:
            print(
                'm3u8.py <m3u8_url> <download_path> -o <out_file> -s <start_time> -e <end_time> -f <start_file> -g <end_file> [-u]')
            sys.exit(2)
        else:
            cm3u8url = argv[1]  # 下载地址
            cpath = argv[2]  # 下载路径
            proset = argv[3:]  # 高级配置
    cpath = cpath.replace("\\", "\\\\")  # for windows
    cthread = 25  # 线程数
    outfile = ''
    starttime = None  # 开始时间
    endtime = None  # 结束时间
    startfile = None  # 开始文件
    endfile = None  # 结束文件
    automerge = True  # 是否自动合并
    proxy_port = -1
    referer = None
    try:
        # print (argv[3:])
        opts, args = getopt.getopt(proset, "h:t:o:s:e:f:g:p:r:u")
        # print (opts)
    except getopt.GetoptError:
        print(
            '高级配置参数: -o <out_file> -s <start_time> -e <end_time> -f <start_file> -g <end_file> -p <proxy_port> -r <referer> [-u]')
        sys.exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print(
                'm3u8.py <m3u8_url> <download_path> -o <out_file> -s <start_time> -e <end_time> -f <start_file> -g <end_file> -p <proxy_port> -r <referer> [-u]')
            sys.exit()
        elif opt in ("-t", "--thread"):
            cthread = arg
        elif opt in ("-s", "--starttime"):
            starttime = float(arg)
        elif opt in ("-o", "--outfile"):
            outfile = arg
        elif opt in ("-e", "--endtime"):
            endtime = float(arg)
        elif opt in ("-f", "--startfile"):
            startfile = int(arg)
        elif opt in ("-g", "--endfile"):
            endfile = int(arg)
        elif opt in ("-p", "--proxy_port"):
            proxy_port = int(arg)
        elif opt in ("-r", "--referer"):
            referer = arg
        elif opt in ("-u", "--unmerge"):
            automerge = False
    print("[Downloading]:", cm3u8url)
    print("[Save Path]:", cpath)
    if outfile != '':
        print("[Out File]:", outfile)
    if starttime:
        print("[Start Time]:", starttime)
    if endtime:
        print("[End Time]:", endtime)
    if startfile:
        print("[Start File]:", startfile)
    if endfile:
        print("[End File]:", endfile)

    downloader = Downloader(cthread, retry=3, proxy_port=proxy_port, referer=referer)
    downloader.run(cm3u8url, cpath, outfile, starttime, endtime, startfile, endfile, automerge)
