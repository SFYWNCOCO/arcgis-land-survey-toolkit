"""Windows当前用户加密凭证行为验证；测试资料不使用真实密钥。"""
from pathlib import Path
import os
import shutil
import subprocess
import json
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_encrypted_service_credential_roundtrip(tmp_path):
    source=ROOT/'addin/AcceptanceAddin/ServiceCredentials.cs'
    assert source.is_file(), '本机加密凭证尚未实现'
    if os.name!='nt': pytest.skip('Windows DPAPI')
    dotnet=shutil.which('dotnet')
    compiler=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/MSBuild/Current/Bin/Roslyn/csc.exe')
    if not dotnet or not compiler.is_file(): pytest.skip('本机Roslyn和.NET6不可用')
    shared=Path(dotnet).parent/'shared'
    runtime=shared/'Microsoft.NETCore.App/6.0.8'
    desktop=shared/'Microsoft.WindowsDesktop.App/6.0.8'
    protected=desktop/'System.Security.Cryptography.ProtectedData.dll'
    harness=tmp_path/'CredentialChecks.cs'
    harness.write_text('''using System; using System.IO; using AcceptanceAddin;
class Checks { static int Main(string[] args) {
 var path=Path.Combine(args[0],"synthetic.key"); var key="synthetic+test/key=&value";
 if(ServiceCredentials.Load(path)!=null) throw new Exception("不存在应返回空");
 ServiceCredentials.Save(key,path);
 if(File.ReadAllText(path).Contains(key)) throw new Exception("文件中存在明文");
 if(ServiceCredentials.Load(path)!=key) throw new Exception("DPAPI读回不一致");
 ServiceCredentials.Save("changed",path);
 if(ServiceCredentials.Load(path)!="changed") throw new Exception("更新密钥失败");
 bool rejected=false; try {ServiceCredentials.Save("",path);} catch(ArgumentException){rejected=true;}
 if(!rejected) throw new Exception("空密钥不应写入");
 Console.WriteLine("CREDENTIAL_BEHAVIOR_OK"); return 0; }}''',encoding='utf-8')
    output=tmp_path/'CredentialChecks.dll'
    refs=[p for p in runtime.glob('*.dll') if (p.name.startswith(('System.','Microsoft.')) or p.name in ('mscorlib.dll','netstandard.dll')) and '.Native.' not in p.name]+[protected]
    response=tmp_path/'compile.rsp'
    response.write_text('\n'.join(['/nologo','/nostdlib','/target:exe','/langversion:latest',f'/out:"{output}"']+
        [f'/reference:"{p}"' for p in refs]+[f'"{source}"',f'"{ROOT / "ai/addin/BasemapPreview.cs"}"',f'"{harness}"']),encoding='utf-8')
    compiled=subprocess.run([str(compiler),'@'+str(response)],capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    output.with_suffix('.runtimeconfig.json').write_text(json.dumps(dict(runtimeOptions=dict(tfm='net6.0',frameworks=[
        dict(name='Microsoft.NETCore.App',version='6.0.8'),dict(name='Microsoft.WindowsDesktop.App',version='6.0.8')]))),encoding='utf-8')
    executed=subprocess.run([dotnet,str(output),str(tmp_path)],capture_output=True,text=True)
    assert executed.returncode==0,executed.stdout+executed.stderr
    assert 'CREDENTIAL_BEHAVIOR_OK' in executed.stdout
