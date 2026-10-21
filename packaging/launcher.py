"""PyInstaller entry point; absolute import works in frozen bundles."""

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    from image_zipper.app import main

    raise SystemExit(main())
