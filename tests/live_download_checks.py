"""Opt-in public download integration: isolated temporary library, no game changes."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vampm.catalog import Catalog
from vampm.providers import Downloads,HUB,trusted_json
from vampm.util import Job

with tempfile.TemporaryDirectory() as td:
    root=Path(td);(root/'game').mkdir();c=Catalog(root/'game',root/'tool');d=Downloads(c)
    refs=['AshAuryn.MacGruber_Life13_Modified_Breathing_Driver.6','Amaimon.Bhaviour.2',
          'AshAuryn.MacGruber_Life13_Modified_Breathing_Driver.7']
    entries=trusted_json(HUB,{'source':'VaM','action':'findPackages','packages':','.join(refs)})['packages']
    rows=[]
    for ref in refs:
        hit=entries[ref]
        item={'filename':hit['filename'],'url':hit['downloadUrl'],'bytes':int(hit.get('file_size') or 0),
              'refs':[ref],'source':'VaM Hub 官方'}
        with mock.patch('vampm.providers.require_game_closed'):
            result=d.download_one(item,Job('public download'))
        row={k:result[k] for k in ('filename','status','bytes','sha256','warnings')}
        rows.append(row);print(json.dumps(row,ensure_ascii=True),flush=True)
    assets=d.sources.releases('acidbubbles/vam-timeline',Job('GitHub'))
    assert any(a['name'].startswith('AcidBubbles.Timeline.') for a in assets)
    print(json.dumps({'github_var_assets':len(assets),'installed_in_temporary_library':c.stats()['files']}),flush=True)
