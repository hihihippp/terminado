import logging
import os.path
import shlex
import ssl
import subprocess
import sys
import threading

# Python3-friendly imports
try:
    import queue
except ImportError:
    import Queue as queue

def command_output(command_args, **kwargs):
    """ Executes a command and returns the string tuple (stdout, stderr)
    keyword argument timeout can be specified to time out command (defaults to 15 sec)
    """
    timeout = kwargs.pop("timeout", 15)
    def command_output_aux():
        try:
            proc = subprocess.Popen(command_args, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            return proc.communicate()
        except Exception as excp:
            return "", str(excp)
    if not timeout:
        return command_output_aux()

    exec_queue = queue.Queue()
    def execute_in_thread():
        exec_queue.put(command_output_aux())
    thrd = threading.Thread(target=execute_in_thread)
    thrd.start()
    try:
        return exec_queue.get(block=True, timeout=timeout)
    except queue.Empty:
        return "", "Timed out after %s seconds" % timeout

def shlex_split_str(line):
    # Avoid NULs introduced by shlex.split when splitting unicode
    return shlex.split(line if isinstance(line, str) else line.encode("utf-8", "replace"))

server_cert_gen_cmds = [
    'openssl req -x509 -nodes -days %(expdays)d -newkey rsa:%(keysize)d -batch -subj /O=pyxterm/CN=%(hostname)s -keyout %(keyfile)s -out %(certfile)s',
    'openssl x509 -noout -fingerprint -in %(certfile)s',
    ]
server_cert_gen_cmds_long = [
    'openssl genrsa -out %(hostname)s.key %(keysize)d',
    'openssl req -new -key %(hostname)s.key -out %(hostname)s.csr -batch -subj "/O=pyxterm/CN=%(hostname)s"',
    'openssl x509 -req -days %(expdays)d -in %(hostname)s.csr -signkey %(hostname)s.key -out %(hostname)s.crt',
    'openssl x509 -noout -fingerprint -in %(hostname)s.crt',
    ]

client_cert_gen_cmds = [
    'openssl genrsa -out %(clientprefix)s.key %(keysize)d',
    'openssl req -new -key %(clientprefix)s.key -out %(clientprefix)s.csr -batch -subj "/O=pyxterm/CN=%(clientname)s"',
    'openssl x509 -req -days %(expdays)d -in %(clientprefix)s.csr -CA %(certfile)s -CAkey %(keyfile)s -set_serial 01 -out %(clientprefix)s.crt',
    "openssl pkcs12 -export -in %(clientprefix)s.crt -inkey %(clientprefix)s.key -out %(clientprefix)s.p12 -passout pass:%(clientpassword)s"
    ]

def ssl_cert_gen(certfile, keyfile="", hostname="localhost", cwd=None, new=False, clientname=""):
    """Return fingerprint of self-signed server certficate, creating a new one, if need be"""
    params = {"certfile": certfile, "keyfile": keyfile or certfile,
              "hostname": hostname, "keysize": 1024, "expdays": 1024,
              "clientname": clientname, "clientprefix":"%s-%s" % (hostname, clientname),
              "clientpassword": "password",}
    cmd_list = server_cert_gen_cmds if new else server_cert_gen_cmds[-1:]
    for cmd in cmd_list:
        cmd_args = shlex_split_str(cmd % params)
        std_out, std_err = command_output(cmd_args, cwd=cwd, timeout=15)
        if std_err:
            logging.warning("pyxterm: SSL keygen %s %s", std_out, std_err)
    fingerprint = std_out
    if new and clientname:
        for cmd in client_cert_gen_cmds:
            cmd_args = shlex_split_str(cmd % params)
            std_out, std_err = command_output(cmd_args, cwd=cwd, timeout=15)
            if std_err:
                logging.warning("pyxterm: SSL client keygen %s %s", std_out, std_err)
    return fingerprint

def prepare_ssl_options(options):
    ssl_options = None
    if options.https or options.client_cert:
        if options.client_cert:
            certfile = options.client_cert
            cert_dir = os.path.dirname(certfile) or os.getcwd()
            if certfile.endswith(".crt"):
                keyfile = certfile[:-4] + ".key"
            else:
                keyfile = ""
        else:
            cert_dir = "."
            server_name = "localhost"
            certfile = os.path.join(cert_dir, server_name+".pem")
            keyfile = ""

        new = not os.path.exists(certfile) and (not keyfile or not os.path.exists(keyfile))
        print("Generating" if new else "Using", "SSL cert", certfile, file=sys.stderr)
        fingerprint = ssl_cert_gen(certfile, keyfile, server_name, cwd=cert_dir, new=new, clientname="term-local" if options.client_cert else "")
        if not fingerprint:
            print("pyxterm: Failed to generate server SSL certificate", file=sys.stderr)
            sys.exit(1)
        print(fingerprint, file=sys.stderr)

        ssl_options = {"certfile": certfile}
        if keyfile:
            ssl_options["keyfile"] = keyfile

        if options.client_cert:
            if options.client_cert == ".":
                ssl_options["ca_certs"] = certfile
            elif not os.path.exists(options.client_cert):
                print("Client cert file %s not found" % options.client_cert, file=sys.stderr)
                sys.exit(1)
            else:
                ssl_options["ca_certs"] = options.client_cert
            ssl_options["cert_reqs"] = ssl.CERT_REQUIRED
    
    return ssl_options
