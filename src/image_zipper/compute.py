"""CPU work runs in a child process so native codecs cannot block Qt's event loop."""

from __future__ import annotations


def run_job(connection, kind, assets, settings, cancel, folder=None, merge_name=None):
    from .imaging import preview_encoded, thumbnail
    from .models import Cancelled, check_cancel
    from .pdf import merge_pdf
    from .service import measure, save_asset, save_merged, save_report

    try:
        if kind == "preview":
            connection.send(("preview", thumbnail(assets[0], edge=900)))
        elif kind in {"estimate", "estimate_merge"}:
            result = (
                merge_pdf(assets, settings, cancel)
                if kind == "estimate_merge"
                else measure(assets[0], settings, cancel)
            )
            check_cancel(cancel)
            preview = preview_encoded(result, edge=900)
            # Transfer metrics and a thumbnail, not a second copy of a large output.
            size = result.size_bytes
            result.data = b""
            connection.send(("estimate", result, size, preview))
        elif kind == "export":
            results, errors, cancelled, report = [], [], False, ""

            def saved(result):
                results.append(result)
                connection.send(("saved", result))

            def progress(index, total, name):
                connection.send(("progress", index, total, name))

            try:
                if merge_name is not None:
                    saved(save_merged(assets, settings, folder, merge_name, cancel, progress))
                else:
                    for index, asset in enumerate(assets):
                        check_cancel(cancel)
                        progress(index, len(assets), asset.path.name)
                        try:
                            save_asset(asset, settings, folder, cancel, saved)
                        except Cancelled:
                            raise
                        except Exception as exc:
                            errors.append(f"{asset.path.name}: {exc}")
                            connection.send(("error", errors[-1]))
            except Cancelled:
                cancelled = True
            except Exception as exc:
                errors.append(str(exc))
                connection.send(("error", errors[-1]))
            if results or errors:
                try:
                    report = str(
                        save_report(
                            folder,
                            results,
                            errors + (["사용자가 취소했습니다."] if cancelled else []),
                        )
                    )
                except Exception as exc:
                    errors.append(f"결과 보고서를 저장할 수 없습니다: {exc}")
            connection.send(("completed", results, errors, report, cancelled))
    except Cancelled:
        pass
    except Exception as exc:
        connection.send(("error", str(exc)))
    finally:
        # Windows Pipe.poll() can raise ERROR_BROKEN_PIPE once the sender
        # closes. End the protocol explicitly before closing its handle.
        connection.send(("done",))
        connection.close()
