# Review AS4 package media before installation

Use this workflow to identify a staged technology package or hardware upgrade by its signed installer and embedded metadata. Use the package's installed catalog and the saved project for the later [workstation and simulation preflight](simulation-preflight.md).

## Locate the matching package

Record the Automation Studio generation and build, the intended package family and selector, and the release information that identifies the exact installer. A firmware or simulator download has its own version. For an ACOPOS 6D target, use the AS4 compatibility row for the intended package family. [B&R AS4 system requirements](https://help.br-automation.com/#/en/4/motion/mapp_6d/allgemeines/systemvoraussetzung.html)

On 2026-11-24, AS4 Automation Help documented mapp 6D 2.7 and 2.8 compatibility rows. The public `1SWMPA6D2` listing ended at installer build `2.4.1.10`. The Help table establishes the version pairing. Use the licensed Automation Studio upgrade catalog or B&R Technical Support to obtain the exact installer and Revision Information for the intended AS4 package. [AS4 compatibility table](https://help.br-automation.com/#/en/4/motion/mapp_6d/allgemeines/systemvoraussetzung.html) · [Public AS4 package catalog](https://www.br-automation.com/en/products/software/mapp-technology/mapp-6d/mapp-6d-licensing/mapp-6d-versions/1swmpa6d2/)

Include the exact Studio and Automation Runtime builds, the controller order number and hardware revision, and the intended simulator, firmware and technology packages in the support request. Ask B&R to confirm the mapp 6D selector, installer build, compatible mapp Motion and mapp View builds, and LaunchPad export version for the same configuration. [B&R Technical Support](https://www.br-automation.com/en-us/service/support-portal/)

## Read an installer without launching it

Start with the original file from the authorized download source. Record its SHA-256, publisher signature result and Windows file version. The example uses a staged copy of mapp Motion 5.29.1.2 and 7-Zip on Windows. [7-Zip](https://www.7-zip.org/)

```powershell
$installer = 'C:\Media\AS4_TP_mappMotion_5.29.1.2.exe'
$archive = 'C:\Program Files\7-Zip\7z.exe'
Get-FileHash -LiteralPath $installer -Algorithm SHA256
Get-AuthenticodeSignature -LiteralPath $installer
(Get-Item -LiteralPath $installer).VersionInfo | Select-Object ProductName, FileVersion, ProductVersion
& $archive l -slt $installer | Select-String '^Path = .*\\ApplicationSettings\\TP\.cfg$'
```

For a signed NSIS installer that 7-Zip can read, use the exact member printed by the listing. Extract only the needed metadata to a fresh review directory. Use the literal member path in single quotes so PowerShell preserves the archive prefix. [B&R mapp Motion 5.29.1.2 download](https://www.br-automation.com/en/downloads/software/automation-studio/tp-upgrades/v43-tp-upgrade-mappmotion-52912/)

```powershell
$member = '$_24_\AS\TechnologyPackages\mappMotion\5.29.1\ApplicationSettings\TP.cfg'
$review = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ('br-media-review-' + [Guid]::NewGuid().ToString('N')))).FullName
& $archive x $installer "-o$review" $member
if ($LASTEXITCODE -ne 0) { throw 'Package metadata extraction failed' }
$manifest = Join-Path $review $member
Get-FileHash -LiteralPath $manifest -Algorithm SHA256
Get-Content -LiteralPath $manifest
```

Compare `InternalName`, `Version` and `InternalVersion` with the intended technology package. In this example, the manifest identifies `mappMotion`, selector `5.29.1` and build `5.29.1.2`. The executable file version is `5.29.1.2`, and its Windows product version is `4.3.4`. Use `Version` for the Automation Studio selector and `InternalVersion` for the package build.

Read the manifest dependencies and lockouts against the proposed Studio, Runtime and other package selectors. For a Core Motion library, read its `SubVersions` bounds and the matching `Library/.../binary.lby` descriptor to identify the actual selectable library version. For a hardware upgrade, list the `.hwx` member under `AS\Hardware\Modules`, then record the exact module ID and revision. The AS4 and AS6 hardware downloads can share a module revision number, as on the controller page below. [B&R controller downloads](https://www.br-automation.com/en-us/products/mechatronic-systems/acopos-6d/controller/8flm11000000i-2/)

Keep the installer and metadata hashes with the acquisition record. After installation, run `simulation_environment` against the actual AS4 root and compare the exact proposed selectors. Use `simulation_preflight` once the project, selected CPU and native export are available. [Installed package checks](simulation-preflight.md#check-a-proposed-package-selection)
