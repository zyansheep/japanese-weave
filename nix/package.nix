# The plugin tree, installed read-only. Everything that writes goes to the state dir (~/.japanese-weave, or
# $JAPANESE_WEAVE_HOME), never next to the code: Python's bytecode is compiled here, unchecked, so nothing tries to
# write it at run time.
{
  lib,
  stdenvNoCC,
  python3,
  nodejs-slim,
  runtimeShell,
  version,
}: let
  fs = lib.fileset;
in
  stdenvNoCC.mkDerivation {
    pname = "japanese-weave";
    inherit version;

    src = fs.toSource {
      root = ../.;
      fileset = fs.unions [
        ../.claude-plugin # the directory marketplace and the plugin manifest
        ../hooks
        ../commands
        ../scripts
        ../data
        ../blocks
        ../docs
        (fs.difference ../extension ../extension/test) # the chat weave runs extension/src; fw extension copies it
        ../LICENSE
        ../README.md
      ];
    };

    buildInputs = [python3]; # for patchShebangs
    dontConfigure = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall
      share=$out/share/japanese-weave
      mkdir -p $share $out/bin $out/lib/japanese-weave/native-messaging
      cp -r .claude-plugin hooks commands scripts data blocks docs extension LICENSE README.md $share/
      echo ${version} > $share/VERSION

      # scripts/run picks a Python from PATH in a checkout; here it is this package's own, with node for the chat
      # weave (chatweave.runtime) ahead of whatever PATH the caller has.
      cat > $share/scripts/run <<EOF
      #!${runtimeShell}
      # Japanese Weave ${version}, installed by Nix: runs a script on the package's own Python, with node for the chat weave.
      PATH="${nodejs-slim}/bin:\$PATH"
      export PATH
      exec ${python3}/bin/python3 "\$@"
      EOF
      chmod 0755 $share/scripts/run

      launcher() {
        printf '#!%s\nexec %s %s "$@"\n' ${runtimeShell} "$share/scripts/run" "$2" > $out/bin/$1
        chmod 0755 $out/bin/$1
      }
      launcher fw "$share/scripts/fw.py"
      launcher fw-host "$share/scripts/fw_host.py"
      launcher japanese-weave-backup "$share/scripts/backup.py --version ${version}"

      # The native messaging manifests, from fw.py's own host_manifest, pointing at this package's launcher.
      for browser in firefox chrome; do
        ${python3}/bin/python3 -B -c '
      import json, sys
      sys.path.insert(0, sys.argv[1])
      import fw
      print(json.dumps(fw.host_manifest(sys.argv[2], sys.argv[3]), indent=1))
      ' "$share/scripts" "$browser" "$out/bin/fw-host" > $out/lib/japanese-weave/native-messaging/$browser.json
      done

      ${python3}/bin/python3 -m compileall -q --invalidation-mode unchecked-hash $share/scripts
      runHook postInstall
    '';

    passthru = {inherit python3;};

    meta = {
      description = "Passive Japanese for people who read a lot of LLM output";
      homepage = "https://github.com/zyansheep/japanese-weave";
      license = lib.licenses.mit;
      mainProgram = "fw";
      platforms = lib.platforms.unix;
    };
  }
