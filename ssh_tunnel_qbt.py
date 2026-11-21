import os
import socket
import select
import threading
import paramiko
from dotenv import load_dotenv

load_dotenv(r"c:\Users\iharg\Projects\New Media Analyser\.env")

user = os.getenv("RUTORRENT_USER")
pwd = os.getenv("RUTORRENT_PASSWORD")
host = "le4-0-106-224fact-core.pulsedmedia.com"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, port=22, username=user, password=pwd)
transport = client.get_transport()

def forward_tunnel(local_port, remote_host, remote_port, transport):
    class SubHandler(threading.Thread):
        def __init__(self, soc, chan):
            super().__init__()
            self.soc = soc
            self.chan = chan

        def run(self):
            while True:
                r, w, x = select.select([self.soc, self.chan], [], [])
                if self.soc in r:
                    data = self.soc.recv(1024)
                    if len(data) == 0:
                        break
                    self.chan.send(data)
                if self.chan in r:
                    data = self.chan.recv(1024)
                    if len(data) == 0:
                        break
                    self.soc.send(data)
            self.chan.close()
            self.soc.close()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", local_port))
    server.listen(5)
    print(f"Forwarding 127.0.0.1:{local_port} to {remote_host}:{remote_port}...")

    while True:
        soc, addr = server.accept()
        try:
            chan = transport.open_channel("direct-tcpip", (remote_host, remote_port), addr)
            if chan is None:
                soc.close()
                continue
            SubHandler(soc, chan).start()
        except Exception as e:
            soc.close()

if __name__ == "__main__":
    forward_tunnel(15626, "127.0.0.1", 15626, transport)
