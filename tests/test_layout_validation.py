from pathlib import Path
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acceptance import map_validation as V


def element(name, kind='TEXT_ELEMENT', x=1, y=9, width=3, height=.3, anchor='TopLeftCorner', **kw):
    return dict(name=name, type=kind, visible=True, x=x, y=y, width=width,
                height=height, anchor=anchor, rotation=0, **kw)


def audit(elements):
    return V.audit_layout_snapshot(dict(width=21, height=29.7, units='CENTIMETER', elements=elements))


def test_layout_detects_long_point_text_beyond_paper():
    result = audit([element('图名', x=10.5, y=28, width=25, anchor='TopMidPoint', text='长标题')])
    assert any(c['item'] == '纸面边界' and c['status'] == 'FAIL' for c in result['checks'])


def test_layout_distinguishes_potential_overlap_from_definite_text_overflow():
    result = audit([element('图名'), element('Legend', kind='LEGEND_ELEMENT', y=9.1, height=2),
                    element('面积行', x=10, overflowing=True)])
    assert any(c['item'] == '版面压盖' and c['status'] == 'NEEDS_REVIEW' for c in result['checks'])
    assert any(c['item'] == '文字溢出' and c['status'] == 'FAIL' for c in result['checks'])


def test_main_map_and_decorative_group_are_not_obstacles_to_map_annotations():
    result = audit([element('WEBMAP_MAP_FRAME', kind='MAPFRAME_ELEMENT', x=1, y=1,
                            width=19, height=26, anchor='BottomLeftCorner'),
                    element('外图廓', kind='MAPFRAME_ELEMENT', x=.5, y=.5, width=20, height=27,
                            anchor='BottomLeftCorner'),
                    element('Scale bar', kind='GRAPHIC_ELEMENT', x=10, y=5, width=5, height=1),
                    element('Scale Line', kind='MAPSURROUND_ELEMENT', x=10, y=5, width=5, height=1),
                    element('图内说明', x=6, y=12)])
    assert not any(c['item'] == '版面压盖' for c in result['checks'])


def test_inset_over_north_arrow_is_reported():
    result = audit([element('区位图框', kind='MAPFRAME_ELEMENT', x=17, y=24, width=3, height=3,
                            anchor='BottomLeftCorner'),
                    element('North Arrow', kind='MAPSURROUND_ELEMENT', x=18.7, y=25.9,
                            width=1.6, height=1.6, anchor='CenterPoint')])
    assert any(c['item'] == '版面压盖' and set(c['elements']) == {'区位图框', 'North Arrow'}
               for c in result['checks'])


def test_unknown_anchor_and_paragraph_overflow_remain_unverified():
    result = audit([element('面积表', anchor=None, text_geometry='paragraph')])
    assert result['status'] == 'NEEDS_REVIEW'
    assert any(c['item'] == '文字溢出' and c['status'] == 'NEEDS_REVIEW' for c in result['checks'])


def test_snapshot_reads_cim_only_for_anchor_without_mutating_layout():
    class Layout:
        pageWidth, pageHeight, pageUnits = 21, 29.7, 'CENTIMETER'
        def listElements(self):
            return [NS(name='图名', type='TEXT_ELEMENT', visible=True, text='标题',
                       elementPositionX=10.5, elementPositionY=28, elementWidth=5, elementHeight=.5)]
        def getDefinition(self, version):
            assert version == 'V3'
            return NS(elements=[NS(name='图名', anchor='TopMidPoint', rotation=0,
                                   graphic=NS(shape={'x': 10.5, 'y': 28}))])
    snapshot = V.collect_layout_snapshot(Layout())
    assert snapshot['elements'][0]['anchor'] == 'TopMidPoint'
    assert snapshot['elements'][0]['text_geometry'] == 'point'
