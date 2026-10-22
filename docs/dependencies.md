# 배포 의존성과 라이선스

앱의 직접 의존성은 `pyproject.toml`, 코덱 빌드 도구는 `requirements-build.txt`, 해당 버전의 소스는 `packaging/third-party-sources.toml`에 고정합니다. 앱의 **도움말 → 오픈소스 라이선스**에서 라이선스와 실제 설치 버전(`versions.txt`)을 확인할 수 있습니다. 사용자 출력 폴더에는 결과 이미지/PDF만 저장합니다.

| 구성요소 | 용도 | 배포 조건 |
|---|---|---|
| PySide6 / shiboken6 / Qt | GUI, 시스템 창 장식 | LGPLv3, 공유 라이브러리와 해당 버전 소스 제공 |
| Pillow와 내장 코덱 | 이미지 읽기·리샘플링·저장 | 각 구성요소의 저작권·라이선스 원문 포함 |
| pillow-heif / libheif / libde265 | 최신 HEIC 디코딩 | BSD / LGPLv3, 인코더 없는 별도 빌드 |
| pyjpegli / Jpegli | JPEG 인코딩 | BSD 및 하위 구성요소 고지 포함 |
| pyoxipng / OxiPNG | PNG 무손실 최적화 | MIT |
| pikepdf / qpdf | PDF 구조와 이미지 최적화 | MPLv2 / Apachev2, pikepdf 소스 제공 |
| img2pdf | 이미지 PDF 병합 | LGPLv3, 소스 및 재빌드 안내 제공 |
| pypdfium2 / PDFium | PDF 미리보기 | Apache/BSD 및 전체 PDFium 고지 포함 |
| Python / PyInstaller | 실행 환경·패키징 | PSF / GPL과 부트로더 예외 조항 포함 |

HEIC 라이브러리의 일반 배포 휠에는 GPL 인코더가 포함됩니다. Pictumi 배포본은 `scripts/build_heif_decoder.py`로 같은 최신 libheif/libde265 버전을 공유 라이브러리로 빌드하고 x265·x264 등 인코더를 제외합니다. HEIC 입력은 유지하며, HEIC 출력은 제공하지 않습니다. 테스트용 HEIC는 직접 생성한 도형 이미지로 실제 사진이나 개인정보가 없습니다. 빌드는 인코더가 남아 있으면 중단됩니다.

`scripts/licenses.py`는 패키지 라이선스 디렉터리 전체, Qt 소스의 라이선스·저작권·하위 구성요소 고지를 수집합니다. 파일 이름에 LICENSE가 없는 LGPL/Apache 원문도 포함합니다. 라이선스 고지와 동일 릴리스의 소스 묶음은 [GitHub Releases](https://github.com/lgyEthan/pictumi/releases)에서 함께 배포합니다. 소스 묶음 생성:

```bash
python scripts/licenses.py --source-archive dist/Pictumi-v0.1.7-third-party-sources.zip
```

Qt 모듈은 Core, Gui, Widgets, Network, DBus, Svg 및 이미지 형식 플러그인으로 제한합니다. 폴더형 배포에서 라이브러리를 교체할 수 있으며, 라이브러리 수정·교체와 그 디버깅에 필요한 역공학을 제한하지 않습니다. 자세한 재빌드·서명 안내는 `packaging/REBUILDING.txt`에 있습니다. 배포용 인증서·키·공증 자격증명은 저장소와 배포본에 포함하지 않습니다.

앱 아이콘과 UI의 단순 도형은 Pictumi용으로 직접 제작했습니다. Apple·Instagram의 아이콘, 로고, 사진, 독점 리소스를 복제해 배포하지 않습니다. 원본 제작 내역은 `src/pictumi/assets/ORIGIN.txt`에 있습니다. 공개 이름 검색은 동일 이름 확인에 도움을 주지만, 모든 관할 지역의 상표 등록 조사를 대신하지는 않습니다.
