import sys
sys.path.insert(0,'src')
from papervoice.room_presets import *
def test_validation_and_projection():
    x=validate_presets([{'id':'marketing-123','name':'  Marketing  ','agentIds':['a','a','b']}])
    assert x[0]['name']=='Marketing' and x[0]['agentIds']==['a','b']
    assert project_preset(x[0],['a']).stale_agent_ids==('b',)
def test_unique_casefold():
    try: validate_presets([{'id':'marketing-123','name':'Sales','agentIds':['a']},{'id':'sales-1234','name':' sales ','agentIds':['b']}])
    except ValueError: return
    assert False
