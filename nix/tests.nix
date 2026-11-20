# The Python tests against the installed package: a work folder links the package's tree (read-only, in the store)
# next to copies of tests/ and tools/, so every script runs from the store, on the package's own Python and node, and
# anything that tried to write next to the code would fail here.
{
  runCommand,
  git,
  japanese-weave,
  tests,
  tools,
}:
runCommand "japanese-weave-tests-${japanese-weave.version}" {nativeBuildInputs = [git];} ''
  share=${japanese-weave}/share/japanese-weave
  mkdir work
  cd work
  for entry in $share/* $share/.claude-plugin; do ln -s "$entry" .; done
  cp -r ${tests} tests
  cp -r ${tools} tools # the simulated reader finds its files from its own resolved path
  chmod -R u+w tests tools
  export HOME=$TMPDIR/home JAPANESE_WEAVE_HOME=$TMPDIR/state
  mkdir -p $HOME
  sh $share/scripts/run -m unittest discover -s tests -q
  ${japanese-weave}/bin/fw --help > /dev/null
  touch $out
''
