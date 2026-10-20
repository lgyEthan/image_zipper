"""PyInstaller entry point; absolute import works in frozen bundles."""

from image_zipper.app import main

if __name__ == "__main__":
    raise SystemExit(main())
