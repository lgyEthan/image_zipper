# 배포 의존성

앱의 직접 의존성은 `pyproject.toml`에 정확한 버전으로 고정되어 있습니다. 빌드 중 설치된 패키지의 LICENSE/COPYING/NOTICE 파일을 `third-party-licenses/`로 수집하고 실제 버전을 `versions.json`에 기록합니다. 전이 의존성은 패키지의 요구 범위에 따라 설치됩니다.

| 구성요소 | 용도 | 공식 프로젝트 |
|---|---|---|
| PySide6 Essentials / shiboken6 | 네이티브 데스크톱 GUI | https://doc.qt.io/qtforpython-6/ |
| Pillow | 디코딩·리샘플링·JPEG/WebP/AVIF/TIFF 저장 | https://github.com/python-pillow/Pillow |
| pillow-heif / libheif | HEIC/HEIF | https://github.com/bigcat88/pillow_heif |
| pyjpegli / Jpegli | JPEG 인코딩 | https://github.com/Maksim-Burtsev/pyjpegli |
| pyoxipng / OxiPNG | PNG 무손실 최적화 | https://github.com/nfrasser/pyoxipng |
| pikepdf / qpdf | PDF 구조·이미지 스트림 최적화 | https://github.com/pikepdf/pikepdf |
| img2pdf | 이미지의 PDF 페이지 삽입 | https://gitlab.mister-muffin.de/josch/img2pdf |
| pypdfium2 / PDFium | 미리보기 | https://github.com/pypdfium2-team/pypdfium2 |
| PyInstaller | 독립 실행 파일 패키징 | https://pyinstaller.org/ |

PySide6/Qt 등은 라이브러리 파일을 교체할 수 있는 폴더형 번들로 배포합니다. 개발 빌드에는 운영체제별 배포 인증서나 공증 자격증명이 포함되지 않습니다. 현재 버전은 별도 Ghostscript 설치나 시스템 코덱 설치를 요구하지 않습니다.
