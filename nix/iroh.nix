# iroh's Python bindings aren't in nixpkgs; use the prebuilt wheel (a Rust library behind uniffi).
{
  lib,
  stdenv,
  fetchurl,
  autoPatchelfHook,
  python3Packages,
}:

let
  version = "1.2.0";
  wheels = {
    x86_64-linux = {
      platform = "manylinux_2_28_x86_64";
      hash = "sha256:61222adc81745009f34ca1fed42633f6cac865c9ed35a4701eb8c0174cb8399a";
    };
    aarch64-linux = {
      platform = "manylinux_2_28_aarch64";
      hash = "sha256:049eef820a5710093cb0cc9d645da00f69dfbf5b0ac7647741da2168b79d0c10";
    };
  };
  wheel = wheels.${stdenv.hostPlatform.system};
in
python3Packages.buildPythonPackage {
  pname = "iroh";
  inherit version;
  format = "wheel";

  src = fetchurl {
    url = "https://files.pythonhosted.org/packages/py3/i/iroh/iroh-${version}-py3-none-${wheel.platform}.whl";
    inherit (wheel) hash;
  };

  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  pythonImportsCheck = [ "iroh" ];

  meta = {
    description = "Python bindings for iroh: dial peers by public key";
    homepage = "https://github.com/n0-computer/iroh-ffi";
    license = with lib.licenses; [ mit asl20 ];
    platforms = builtins.attrNames wheels;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
