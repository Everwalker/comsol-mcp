from __future__ import annotations
import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import next_tasks as n

class PlanTests(unittest.TestCase):
    def fixture(self):
        plan={'tasks':[
            {'id':'T01','group':'G0','task':'source','depends_on':[]},
            {'id':'T02','group':'G1','task':'software downstream','depends_on':['T01']},
            {'id':'T03','group':'G9','task':'freeze','depends_on':['T02'],
             'validation_check_dependencies':['native/windows']}]}
        state={'tasks':{t['id']:{'status':'TODO','implementation_done':False,'evidence_refs':[]} for t in plan['tasks']},
               'target_checks':{'native/windows':{'status':'TODO','evidence_refs':[]}}}
        return plan,state

    def test_blocked_native_does_not_block_implemented_software_dependencies(self):
        p,s=self.fixture()
        s['tasks']['T01'].update(status='BLOCKED',implementation_done=True,blocker='native host missing')
        r=n.navigate(p,s)
        self.assertEqual([x['id'] for x in r['ready']],['T02'])
        s['tasks']['T01']['implementation_done']=False
        self.assertEqual(n.navigate(p,s)['ready'],[])

    def test_native_gate_and_final_truth_never_inferred_from_implementation(self):
        p,s=self.fixture()
        for t in ['T01','T02']:s['tasks'][t].update(status='IMPLEMENTED',implementation_done=True)
        r=n.navigate(p,s)
        self.assertNotIn('T03',[x['id'] for x in r['ready']])
        s['target_checks']['native/windows']={'status':'DONE','evidence_refs':['actual-host-report']}
        self.assertIn('T03',[x['id'] for x in n.navigate(p,s)['ready']])
        for t in s['tasks']:s['tasks'][t]={'status':'DONE','evidence_refs':['scoped-result']}
        r=n.navigate(p,s)
        self.assertEqual(r['finished_execution_steps'],3)
        self.assertEqual(r['product_acceptance'],'NOT_ESTABLISHED_BY_THIS_TOOL')

    def test_cycles_bad_dependencies_and_malformed_state_refused(self):
        p,s=self.fixture()
        cycle=copy.deepcopy(p);cycle['tasks'][0]['depends_on']=['T03']
        with self.assertRaises(ValueError):n.validate_plan(cycle)
        unknown=copy.deepcopy(p);unknown['tasks'][0]['depends_on']=['T99']
        with self.assertRaises(ValueError):n.validate_plan(unknown)
        s['tasks']['T01']['status']='PASS'
        with self.assertRaises(ValueError):n.navigate(p,s)
        s['tasks']['T01']['status']='DONE'
        with self.assertRaises(ValueError):n.navigate(p,s)

    def test_failed_real_attempt_opens_repair_without_accepting_failed_task(self):
        p,s=self.fixture()
        p['tasks'][1]['depends_on']=[]
        p['tasks'][1]['attempt_dependencies']=['T01']
        s['tasks']['T01'].update(status='BLOCKED',execution_complete=True,evidence_refs=['actual-failure'],blocker='core bug')
        r=n.navigate(p,s)
        self.assertIn('T02',[x['id'] for x in r['ready']])
        self.assertEqual(s['tasks']['T01']['status'],'BLOCKED')
        self.assertEqual(r['finished_execution_steps'],0)
        s['tasks']['T01']['execution_complete']=False
        self.assertNotIn('T02',[x['id'] for x in n.navigate(p,s)['ready']])

    def test_actual_plan_is_acyclic_complete_and_template_matches(self):
        plan=json.loads((ROOT/'TASKS.json').read_text())
        state=json.loads((ROOT/'state/PROGRESS_TEMPLATE.json').read_text())
        dod=json.loads((ROOT/'config/UNIVERSAL_DOD.json').read_text())
        n.load_state(plan,state)
        self.assertEqual({u for t in plan['tasks'] for u in t['requirement_groups']},{g['id'] for g in dod['groups']})
        self.assertEqual(len(plan['tasks']),52)
        self.assertEqual([x['id'] for x in n.navigate(plan,state)['ready']],['T01'])

if __name__=='__main__':unittest.main()
