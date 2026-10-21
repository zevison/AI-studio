"""Build a pinned offline plugin package; network is opt-in, no cloud publication."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.component_manager import embedded_catalog as catalog
from core.component_download import fetch
from core.component_packages import export_pack


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources',type=Path,required=True,help='Built _internal or captured resource directory with components-profile.json')
    parser.add_argument('--id',choices=('asr','browser'),required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--download',action='store_true',help='Explicitly obtain missing public pinned bytes, never call a model')
    args=parser.parse_args()
    item=catalog(args.resources)[args.id]
    if args.download:
        for row in item['files']:fetch(row,args.cache)
    result=export_pack(args.output,args.id,item,args.cache)
    print(str(result))

if __name__=='__main__':main()
