{ lib, python3Packages, callPackage }:

python3Packages.buildPythonApplication {
  pname = "kanso";
  version = (lib.importTOML ../pyproject.toml).project.version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../README.md
      ../src
      ../tests
    ];
  };

  build-system = [ python3Packages.hatchling ];
  dependencies = with python3Packages; [
    (callPackage ./iroh.nix { })
    platformdirs
    sqlmodel
    textual
    textual-serve
  ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pythonImportsCheck = [ "kanso" ];

  meta = {
    description = "Personal, local-first todo app with a terminal UI, also served in the browser";
    mainProgram = "kanso";
  };
}
