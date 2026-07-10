import subprocess, sys, pathlib

project = pathlib.Path('/Users/teodorhiidenlampi/Desktop/Hermes ai/google-ai-prospect-agent')
result = subprocess.run([sys.executable, 'cli.py', '--help'], cwd=project, capture_output=True, text=True)
print('exit:', result.returncode)
print('stdout:', result.stdout[:200])
print('stderr:', result.stderr[:200])
if result.returncode != 0:
    sys.exit(1)
