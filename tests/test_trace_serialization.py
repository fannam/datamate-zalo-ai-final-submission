import json
from decimal import Decimal

import pytest

from datamate.io import dumps,write_json


def test_trace_nonfinite_keeps_evidence_without_breaking_run(tmp_path):
    path=tmp_path/'trace.json'
    value={'executions':[{'rows':[(float('nan'),Decimal('Infinity'),10)]}]}
    write_json(path,value)
    loaded=json.loads(path.read_text())
    assert loaded['executions'][0]['rows']==[[None,None,10]]
    assert len(loaded['_nonfinite_values'])==2
    assert loaded['_nonfinite_values'][0]['path']=='$.executions[0].rows[0][0]'
    # Submission JSON remains strict; only artifact logging gets the recovery.
    with pytest.raises(ValueError):
        dumps({'id':'x','type':'number','answer':float('nan')})
