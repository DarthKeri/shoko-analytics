"""BF marks cover only written/recalculated old cells, never the live sheet."""
import copy
import json
import unittest
from unittest.mock import Mock,patch
from shoko.sheet_backups import highlight_ranges,highlight_rule,verify_highlight_rule
from shoko.adapters import GoogleTarget
from shoko.storage import AppError


def fixture():
    source=dict(id=7,rows=5,cols=6,raw=[['Header'],[1,2,3],[4,5,6]],values=[['Header'],[1,2,3],[4,5,6]])
    plan=dict(before={'sheets':{'Old':source}},changes=[],operations=[],derived=[],sheet_backups=[{'source':'Old','target':'BF_Old'}])
    return plan,{'sheets':{'Old':copy.deepcopy(source)}},{'sheets':{'BF_Old':dict(copy.deepcopy(source),id=10)}}


def cells(areas):
    return {(r,c) for a in areas for r in range(a['startRowIndex'],a['endRowIndex']) for c in range(a['startColumnIndex'],a['endColumnIndex'])}


class BackupHighlightTests(unittest.TestCase):
    def test_exact_rectangles_leave_holes_and_clip_new_cells_to_old_grid(self):
        plan,result,_=fixture()
        for r,c in [(2,1),(2,3),(3,1),(3,3),(5,6),(6,1),(2,7)]:
            plan['changes'].append(dict(sheet='Old',row=r,col=c,after=''))
        original=copy.deepcopy(plan)
        areas=highlight_ranges(plan,'Old',result)
        self.assertEqual(cells(areas),{(1,0),(1,2),(2,0),(2,2),(4,5)})
        self.assertEqual(len(areas),3)
        self.assertEqual(plan,original)
        self.assertEqual(highlight_ranges(plan,'Old',result),areas)

    def test_formula_writes_are_marked_even_when_result_is_the_same(self):
        plan,result,_=fixture();plan['changes']=[dict(sheet='Old',row=2,col=2,kind='formula',after='=1+1')]
        self.assertEqual(cells(highlight_ranges(plan,'Old',result)),{(1,1)})

    def test_recalculated_results_and_cleared_cells_are_marked(self):
        plan,result,_=fixture();result['sheets']['Old']['values'][1]=[1,20,'']
        plan['derived']=[dict(sheet='Old',row=3,col=2,value=50)]
        self.assertEqual(cells(highlight_ranges(plan,'Old',result)),{(1,1),(1,2),(2,1)})

    def test_equal_numbers_blanks_and_unrelated_sheets_are_not_marked(self):
        plan,result,_=fixture();result['sheets']['Old']['values'][1]=[1.0,2,3]
        result['sheets']['Old']['values'][0]=['Header',None]
        plan['changes']=[dict(sheet='Other',row=2,col=1,after=10)]
        plan['derived']=[dict(sheet='Old',row=2,col=1,value=1.0)]
        self.assertEqual(highlight_ranges(plan,'Old',result),[])
        result['sheets']['Old']['values'][1][0]=True
        self.assertEqual(cells(highlight_ranges(plan,'Old',result)),{(1,0)})

    def test_renamed_garage_uses_old_bf_coordinates_and_new_active_values(self):
        plan,result,_=fixture();plan['operations']=[dict(kind='rename',source='Old',target='New')]
        result['sheets']['New']=result['sheets'].pop('Old');result['sheets']['New']['values'][2][2]=60
        plan['changes']=[dict(sheet='New',row=2,col=1,after=10)]
        self.assertEqual(cells(highlight_ranges(plan,'Old',result)),{(1,0),(2,2)})

    def test_large_contiguous_region_is_one_small_rule(self):
        plan,result,_=fixture();s=plan['before']['sheets']['Old'];s.update(rows=1000,cols=100,values=[])
        result['sheets']['Old']['values']=[[1]*100 for _ in range(1000)]
        areas=highlight_ranges(plan,'Old',result)
        self.assertEqual(areas,[dict(startRowIndex=0,endRowIndex=1000,startColumnIndex=0,endColumnIndex=100)])
        self.assertLess(len(json.dumps(highlight_rule(areas,10))),600)

    def test_rule_verification_accepts_google_color_rounding_and_zero_defaults(self):
        rule=highlight_rule([dict(startRowIndex=0,endRowIndex=2,startColumnIndex=0,endColumnIndex=1)],10)
        actual=copy.deepcopy(rule)
        actual['ranges'][0].pop('startRowIndex');actual['ranges'][0].pop('startColumnIndex')
        fmt=actual['booleanRule']['format']
        self.assertEqual(set(fmt),{'backgroundColor','textFormat'})
        self.assertEqual(set(fmt['textFormat']),{'foregroundColor'})
        for color in (fmt['backgroundColor'],fmt['textFormat']['foregroundColor']):
            for k in color:color[k]=round(color[k],8)
        verify_highlight_rule(actual,rule,'BF_Old')
        for key,value in [('endRowIndex',3),('sheetId',7)]:
            bad=copy.deepcopy(actual);bad['ranges'][0][key]=value
            with self.subTest(key=key),self.assertRaises(AppError):verify_highlight_rule(bad,rule,'BF_Old')
        bad=copy.deepcopy(actual);bad['booleanRule']['format']['backgroundColor']['red']=0
        with self.assertRaises(AppError):verify_highlight_rule(bad,rule,'BF_Old')
        with self.assertRaises(AppError):verify_highlight_rule({},rule,'BF_Old')

    def test_format_request_targets_only_new_bf_and_preserves_underlying_rules(self):
        plan,result,copies=fixture();plan['changes']=[dict(sheet='Old',row=2,col=2,after=20)]
        result['sheets']['Old']['values'][2][2]=60
        expected=highlight_rule(highlight_ranges(plan,'Old',result),10)
        adapter=object.__new__(GoogleTarget);adapter.request=Mock(side_effect=[{},dict(sheets=[dict(properties={'title':'BF_Old','sheetId':10},conditionalFormats=[expected,{'original':'rule'}])])])
        adapter.highlight_backups(plan,result,copies)
        call=adapter.request.call_args_list[0]
        requests=json.loads(call.kwargs['data'])['requests']
        self.assertEqual(requests,[{'addConditionalFormatRule':{'rule':expected,'index':0}}])
        self.assertTrue(all(r['sheetId']==10 for r in expected['ranges']))
        self.assertFalse(any('deleteConditionalFormatRule' in r or 'updateCells' in r for r in requests))

    def test_sparse_regions_are_split_into_bounded_rules_in_verified_order(self):
        plan,result,copies=fixture()
        areas=[dict(startRowIndex=n,endRowIndex=n+1,startColumnIndex=n%2,endColumnIndex=n%2+1) for n in range(1200)]
        rules=[highlight_rule(areas[i:i+500],10) for i in range(0,len(areas),500)]
        adapter=object.__new__(GoogleTarget)
        adapter.request=Mock(side_effect=[{},dict(sheets=[dict(properties={'title':'BF_Old'},conditionalFormats=list(reversed(rules)))])])
        with patch('shoko.sheet_backups.highlight_ranges',return_value=areas):adapter.highlight_backups(plan,result,copies)
        requests=json.loads(adapter.request.call_args_list[0].kwargs['data'])['requests']
        self.assertEqual([len(r['addConditionalFormatRule']['rule']['ranges']) for r in requests],[500,500,200])

    def test_failed_format_post_is_not_retried(self):
        plan,result,copies=fixture();plan['changes']=[dict(sheet='Old',row=2,col=2,after=20)]
        adapter=object.__new__(GoogleTarget);adapter.request=Mock(side_effect=AppError('not confirmed'))
        with self.assertRaises(AppError):adapter.highlight_backups(plan,result,copies)
        self.assertEqual(adapter.request.call_count,1)

    def test_zero_change_does_not_issue_format_requests(self):
        plan,result,copies=fixture();adapter=object.__new__(GoogleTarget);adapter.request=Mock()
        adapter.highlight_backups(plan,result,copies);adapter.request.assert_not_called()

    def test_missing_format_after_successful_post_is_reported(self):
        plan,result,copies=fixture();plan['changes']=[dict(sheet='Old',row=2,col=2,after=20)]
        adapter=object.__new__(GoogleTarget);adapter.request=Mock(side_effect=[{},dict(sheets=[])])
        with self.assertRaisesRegex(AppError,'подсветка BF'):adapter.highlight_backups(plan,result,copies)
        self.assertEqual(adapter.request.call_count,2)


if __name__=='__main__':unittest.main()
