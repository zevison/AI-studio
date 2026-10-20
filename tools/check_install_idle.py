"""Fail before runtime extraction if this installation has a live workspace."""
from install_analysis_stack import studio_is_running

if __name__ == '__main__':
    if studio_is_running():
        print('Studio is running. Stop all workspaces normally before installation; no files were replaced.')
        raise SystemExit(11)
    print('No running Studio workspace detected in this installation.')
