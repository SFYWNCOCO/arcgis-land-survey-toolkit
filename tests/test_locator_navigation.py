"""Execute the add-in's pure C# navigation and real QA schema handling."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "addin/AcceptanceAddin/ParcelNavigation.cs"
LOCATOR_WINDOW = ROOT / "addin/AcceptanceAddin/ParcelLocatorWindow.cs"
LIVE_PROTOCOL_SOURCE = ROOT / "addin/AcceptanceAddin/LiveMcpProtocol.cs"


def test_locator_navigation_and_qa_contract(tmp_path):
    assert SOURCE.is_file(), "范围导航/QA定位逻辑尚未实现"
    compiler = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/2022/BuildTools/MSBuild/Current/Bin/Roslyn/csc.exe"
    dotnet = shutil.which("dotnet")
    if not compiler.exists() or not dotnet:
        pytest.skip("需要本机Roslyn和.NET 6，不能代替Pro实机点击验收")
    runtimes = Path(dotnet).parent / "shared/Microsoft.NETCore.App"
    runtime = max((path for path in runtimes.iterdir() if path.name.startswith("6.0.")), key=lambda path: tuple(map(int, path.name.split('.'))))
    harness = tmp_path / "LocatorChecks.cs"
    harness.write_text(r'''
using System;
using System.Linq;
using System.Text.Json;
using AcceptanceAddin;
class Checks {
 static void Assert(bool ok, string reason) { if (!ok) throw new Exception(reason); }
 static ParcelReference Row(string project, string uri, long oid, string parcel, string unit="U1", string point="") {
   return new ParcelReference { ProjectId=project, LayerUri=uri, Oid=oid, ParcelId=parcel, UnitId=unit, PointId=point };
 }
 static int Main() {
   var records = new[] { Row("B", "layer1", 1, "DK1"), Row("A", "layer1", 3, "DK3"), Row("A", "layer1", 2, "DK2"), Row("A", "same-name-different-uri", 7, "DK7") };
   var scope = ParcelNavigation.Partition(records, "A", "layer1");
   Assert(scope.Count==2 && scope[0].Oid==2 && scope[1].Oid==3, "只遍历所选项目和URI，排序稳定");
   Assert(ParcelNavigation.NextIndex(-1, 1, 2)==0, "开始时到第一条");
   Assert(ParcelNavigation.NextIndex(0,-1,2)==0 && ParcelNavigation.NextIndex(1,1,2)==1, "首尾不循环、不跨项目");
   Assert(ParcelNavigation.NextIndex(-1,1,0)==-1, "空范围无虚构对象");
   using var qa=JsonDocument.Parse(@"{""status"":""FAIL"",""unit_checks"":[{""unit_id"":""U1"",""status"":""PASS""},{""unit_id"":""U2"",""status"":""FAIL""}],""parcel_checks"":[{""unit_id"":""U1"",""parcel_id"":""DK2"",""status"":""FAIL""}],""spacing_violations"":[{""unit_id"":""U1"",""point_id_a"":""P1"",""point_id_b"":""P2"",""parcel_id_a"":""DK1"",""parcel_id_b"":""DK2""}],""duplicate_point_ids"":[""P9""],""manual_review_required"":[""无具体编号的一般说明""]}");
   var issues=ParcelNavigation.ReadQa(qa.RootElement,"采样质检");
   Assert(issues.Count==5, "仅问题行；间距对拆为两点，不把通用说明假装成定位对象");
   Assert(issues.Single(i=>i.Category=="unit_checks").UnitId=="U2", "排除PASS检查");
   var point=issues.Single(i=>i.PointId=="P2");
   Assert(point.ParcelId=="DK2" && point.UnitId=="U1", "成对编号保留所属单元和图斑");
   Assert(ParcelNavigation.Matches(point,Row("A","l",2,"DK2","U1","P2")), "按真实编号匹配");
   Assert(!ParcelNavigation.Matches(point,Row("B","l",2,"DK2","U2","P2")), "相同点号不同单元不得误跳");
   Assert(!ParcelNavigation.Matches(point,Row("A","l",2,"DK1","U1","P2")), "相同点号不同图斑不得误跳");
   using var bulk=JsonDocument.Parse(@"{""unit_checks"":[{""unit_id"":""U1"",""status"":""PASS""}],""spacing_deviations"":[{""unit_id"":""U1"",""replicate_no_a"":1,""replicate_no_b"":3,""sample_id"":""RZU1"",""sample_id_other"":""RZU1""}],""duplicate_profiles"":[""U8""]}");
   var bulkIssues=ParcelNavigation.ReadQa(bulk.RootElement,"容重质检",true);
   Assert(bulkIssues.Count==3, "容重间距问题保留两平行样和调查单元");
   var rep=bulkIssues.Single(i=>i.ReplicateNo=="3");
   Assert(ParcelNavigation.Matches(rep,new ParcelReference { UnitId="U1", SampleId="RZU1", ReplicateNo="3" }), "容重三元身份定位");
   Assert(!ParcelNavigation.Matches(rep,new ParcelReference { UnitId="U1", SampleId="RZU1", ReplicateNo="1" }), "不能跳到另一平行样");
   using var report=JsonDocument.Parse(@"{""网格单元明细"":[{""评价单元"":""U1"",""达标"":true,""间距达标"":false,""覆盖缺失图斑"":[""DK5""]}],""影像逐单元"":{""U9"":{""coverage_status"":""FAIL""},""U10"":{""coverage_status"":""PASS""}}}");
   var reportIssues=ParcelNavigation.ReadReport(report.RootElement);
   Assert(reportIssues.Any(i=>i.UnitId=="U9" && i.Category=="影像覆盖"), "影像逐单元覆盖失败有定位对象");
   Assert(!reportIssues.Any(i=>i.UnitId=="U10"), "正常影像不入问题列表");
   Assert(ParcelNavigation.SelectionIds(false,new long[0])==null,"全部范围不附加选择条件");
   bool emptyRejected=false;try{ParcelNavigation.SelectionIds(true,new long[0]);}catch(InvalidOperationException){emptyRejected=true;}
   Assert(emptyRejected,"空选择不能退成全部图斑");
   Assert(ParcelNavigation.SelectionIds(true,new long[]{3,2,3}).SequenceEqual(new long[]{2,3}),"选择集去重和排序");
   using var dataQa=JsonDocument.Parse(@"{""已有单元质检"":{""issues"":[{""unit_id"":""U1"",""parcel_id"":""P1"",""other_unit_id"":""U2"",""other_parcel_id"":""P2"",""status"":""FAIL"",""code"":""parcel_overlap""}]}}");
   var dataIssues=ParcelNavigation.ReadReport(dataQa.RootElement);
   Assert(dataIssues.Any(i=>i.UnitId=="U1" && i.ParcelId=="P1") && dataIssues.Any(i=>i.UnitId=="U2" && i.ParcelId=="P2"),"重叠问题两端都能定位");
   Assert(ParcelNavigation.SearchField("评价单元",new[]{"DKBH","PJDYBH"})=="PJDYBH","单元层优先单元号");
   Assert(ParcelNavigation.SearchField("补充耕地图斑",new[]{"DKBH","PJDYBH"})=="DKBH","地块层优先图斑号");
   Assert(ParcelNavigation.SearchField("补充耕地图斑",new[]{"OBJECTID","FID"})==null,"OID不会被当成图斑编号");
   Assert(ParcelNavigation.SearchFieldForLayer("LONG_ID","DKBH",new[]{"DKBH"})==null,"显式选择字段缺失时不得静默回退到默认编号");
   Assert(ParcelNavigation.SearchFieldForLayer("long_id","DKBH",new[]{"LONG_ID","DKBH"})=="LONG_ID","显式字段匹配不区分大小写");
   Assert(ParcelNavigation.SearchFieldForLayer(null,"DKBH",new[]{"DKBH"})=="DKBH","自动模式使用识别出的默认编号");
   Assert(ParcelNavigation.SearchFieldMissingReason("LONG_ID",false)=="不含所选字段","显式字段缺失提示要准确");
   Assert(ParcelNavigation.SearchFieldMissingReason(null,false)=="缺少可识别的业务编号字段","自动模式提示缺少业务编号");
   Assert(ParcelNavigation.SearchFieldMissingReason(null,true)=="缺少对象ID字段","OID模式提示对象ID缺失");
   var fieldCoverage=ParcelNavigation.SearchFieldCoverage(new[]{new[]{"DKBH","LONG_ID","DKBH"},new[]{"dkbh","OTHER"},new[]{"PJDYBH","LONG_ID"}});
   Assert(fieldCoverage.Single(field=>field.Name=="DKBH").LayerCount==2,"字段名大小写不敏感，且同一图层重复列只计一次");
   Assert(fieldCoverage.Single(field=>field.Name=="LONG_ID").LayerCount==2,"非标准业务编号跨图层可供筛选");
   Assert(fieldCoverage.Count==4 && fieldCoverage[0].Name=="DKBH","字段按覆盖图层数降序、再按名称稳定排序");
   Assert(ParcelNavigation.ResolveSearchField("dkbh",new[]{"DKBH","PJDYBH"})=="DKBH","自定义字段匹配不受大小写差异影响");
   var pointIssue=Row("A","layer1",1,"DK1","U1","P1");
   Assert(ParcelNavigation.HasIssueFields(pointIssue,new[]{"CYDBH"}),"兼容旧采样点层 CYDBH 点号字段");
   Assert(ParcelNavigation.HasIssueFields(pointIssue,new[]{"POINT_ID"}),"兼容标准 POINT_ID 点号字段");
   Assert(!ParcelNavigation.HasIssueFields(pointIssue,new[]{"OBJECTID"}),"缺少点号字段的图层不参与定位");
   using var confirmed=JsonDocument.Parse(@"{""confirm"":true}");
   using var declined=JsonDocument.Parse(@"{""confirm"":false}");
   using var absent=JsonDocument.Parse(@"{}");
   Assert(LiveMcpProtocol.IsZoomConfirmed(confirmed.RootElement),"桥接确认字段允许明确缩放");
   Assert(!LiveMcpProtocol.IsZoomConfirmed(declined.RootElement) && !LiveMcpProtocol.IsZoomConfirmed(absent.RootElement),"桥接层拒绝缺少或为假的确认");
   var unknown=Row("","layer1",1,"P1","U1");
   Assert(ParcelNavigation.ForNavigation(new[]{unknown,Row("","layer1",2,"P2","U2")},unknown).Count==1,"项目未知不能据同层推定同项目");
   var gate=new NavigationGate();Assert(gate.TryEnter() && !gate.TryEnter(),"同一时刻仅允许一次定位");gate.Exit();Assert(gate.TryEnter(),"结束后可以继续定位");gate.Exit();
   Console.WriteLine("LOCATOR_BEHAVIOR_OK 27 assertions");
   return 0;
 }
}
''', encoding="utf-8")
    output = tmp_path / "LocatorChecks.dll"
    response = tmp_path / "compile.rsp"
    managed_refs = [p for p in runtime.glob('*.dll') if (p.name.startswith('System.') or p.name in ('mscorlib.dll', 'netstandard.dll')) and '.Native.' not in p.name]
    response.write_text("\n".join(["/nologo", "/nostdlib", "/target:exe", "/langversion:latest", f'/out:"{output}"'] + [f'/reference:"{p}"' for p in managed_refs] + [f'"{SOURCE}"', f'"{LIVE_PROTOCOL_SOURCE}"', f'"{harness}"']), encoding="utf-8")
    compiled = subprocess.run([str(compiler), "@"+str(response)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    output.with_suffix(".runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net6.0","framework":{"name":"Microsoft.NETCore.App","version":runtime.name}}}),encoding="utf-8")
    executed = subprocess.run([dotnet,str(output)],capture_output=True,text=True)
    assert executed.returncode==0, executed.stdout+executed.stderr
    assert "LOCATOR_BEHAVIOR_OK" in executed.stdout


def test_locator_does_not_present_an_automatic_oid_as_a_parcel_number():
    source = LOCATOR_WINDOW.read_text(encoding="utf-8")

    assert "fields.First(f => f.FieldType == FieldType.OID)" not in source
    assert "对象ID（技术定位）" in source
    assert 'Header = "地块编号 / 对象ID"' in source
    assert "DisplayId" in source


def test_locator_prefilter_uses_accepted_point_id_aliases():
    source = LOCATOR_WINDOW.read_text(encoding="utf-8")
    assert "ParcelNavigation.HasIssueFields(issue, names)" in source


def test_locator_all_layers_exposes_custom_identifier_fields():
    source = LOCATOR_WINDOW.read_text(encoding="utf-8")
    load_fields = source.split("private async Task LoadFields()", 1)[1].split("private static bool IsIdField", 1)[0]
    search = source.split("private async Task Search(bool loadScope = false)", 1)[1].split("private async Task Locate()", 1)[0]

    assert "SearchFieldCoverage" in load_fields
    assert "_fieldValues" in load_fields
    assert "LayerCount" in load_fields
    assert 'Content = "加载所选图层"' in source
    assert 'Content = "仅检索执行时选中的要素"' in source
    assert "SearchFieldForLayer" in search
    assert "automatic != null && f.Name == automatic" not in search
    assert "SearchFieldMissingReason" in search
