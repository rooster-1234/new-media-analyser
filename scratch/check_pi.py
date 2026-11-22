import os
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('raspberrypi2.local', username='Ian', key_filename=r'C:\Users\iharg\.ssh\raspberrypi2_key.pem')

sftp = ssh.open_sftp()
for f in ["README.md", "CHANGELOG.md", "ROADMAP.md", "app.py", ".env.template"]:
    sftp.put(f, f"/home/Ian/new-media-analyser/{f}")
    print(f"Uploaded {f} to Pi")

try:
    sftp.mkdir("/home/Ian/new-media-analyser/testing")
except Exception:
    pass

for test_file in os.listdir("testing"):
    if test_file.endswith(".py"):
        sftp.put(os.path.join("testing", test_file), f"/home/Ian/new-media-analyser/testing/{test_file}")
print("Uploaded testing/ files to Pi")
sftp.close()

# Restart container to pick up changes
stdin, stdout, stderr = ssh.exec_command('docker restart new-media-analyser && sleep 3 && curl -s http://localhost:8080/dashboard | grep -o "Released"')
print("Pi container restart & verify:\n" + stdout.read().decode())
ssh.close()
print("Pi sync complete.")
