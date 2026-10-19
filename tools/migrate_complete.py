"""Legacy positional entry point, delegated to the safe snapshot migrator."""
from pathlib import Path
import sys
from safe_migrate import migrate

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Pass the explicit old Studio folder')
    try:
        result=migrate(sys.argv[1].strip().strip(chr(34)),Path(__file__).resolve().parents[1])
        print('Migration complete; original installation preserved. Files:',result['copied_files'])
    except Exception as exc:
        print('Migration blocked; existing data preserved:',str(exc));raise SystemExit(2)
