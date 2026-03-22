# karellen-jdtls

Eclipse JDT Language Server distribution with cross-language Java/Kotlin support.

Packages the [karellen-jdtls-kotlin](https://github.com/karellen/karellen-jdtls-kotlin)
search participant plugin into a self-contained jdtls distribution, distributed as
platform-specific Python wheels.

## Installation

```bash
pip install karellen-jdtls
```

This installs the `jdtls` command on your PATH.

## Usage

```bash
jdtls [Eclipse launcher arguments...]
```

The `jdtls` command is a thin launcher that delegates to the Eclipse native launcher
bundled in the wheel. All arguments are passed through.

## Platforms

- Linux x86_64
- Linux aarch64
- macOS x86_64
- macOS aarch64 (Apple Silicon)
- Windows x86_64

## License

Apache License, Version 2.0
