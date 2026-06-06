import os
import sqlite3
import subprocess
import sys

if os.path.exists('pipeline.db'):
    os.remove('pipeline.db')

subprocess.run([sys.executable, 'pipeline.py', 'init'], check=True)
subprocess.run([sys.executable, 'pipeline.py', 'import', 'config_pipeline/examples/config_v1.json'], check=True)

from datetime import datetime, timedelta
now = datetime.now()
start = (now - timedelta(days=1)).isoformat()
end = (now + timedelta(days=1)).isoformat()

subprocess.run([
    sys.executable, 'pipeline.py', 'window', 'create', 'dev',
    start, end, '--reason', 'Test freeze', '--role', 'developer'
], check=True)

result = subprocess.run(
    [sys.executable, 'pipeline.py', 'apply', '1.0.0', 'dev', '--yes'],
    capture_output=True, text=True
)
print('Apply STDERR:', result.stderr)
print('Apply Return code:', result.returncode)

conn = sqlite3.connect('pipeline.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()
cursor.execute('SELECT action, status, error_reason FROM audit_logs ORDER BY id DESC')
print('\nAudit logs:')
for row in cursor.fetchall():
    print('  %-20s %-20s %s' % (row['action'], row['status'], row['error_reason']))

cursor.execute('SELECT command, error_code, error_message FROM error_logs ORDER BY id DESC')
print('\nError logs:')
for row in cursor.fetchall():
    print('  %-20s %-20s %s' % (row['command'], row['error_code'], row['error_message']))
conn.close()

os.remove('pipeline.db')
