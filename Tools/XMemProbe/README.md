# RSC85 native probe

`Rdr1XMemProbe.exe` is a deliberately isolated x86 test harness for the
legacy `xcompress32.dll` API used by older RDR tooling. It does not ship or
redistribute that DLL; point the studio at a copy already installed with the
game/toolchain.

Build the .NET Framework fallback on Windows (the main studio is x64):

```powershell
$csc = "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
& $csc /nologo /platform:x86 /target:exe /out:Rdr1XMemProbe.exe LegacyProbe.cs `
  /r:"$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\System.Web.Extensions.dll"
```

Before trying a game resource, `Rdr1XMemProbe.exe --self-test` verifies that
the native compression and decompression calls round-trip a known buffer. A
resource attempt is one process per candidate:

```text
Rdr1XMemProbe.exe payload.bin 20480 stream [--pad16]
```

The output is JSON. A non-zero exit or native crash means only that candidate
was rejected; it is not a decoded mission. The x64 studio exposes the same
operation as `--probe-xmem` and forwards the child exit code.
