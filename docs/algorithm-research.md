# 이미지·PDF 압축 알고리즘 선택

조사 기준: **2026-10-21**. 공식 구현·문서와 원 논문을 우선했습니다. 아래 수치는 해당 연구의 특정 이미지·설정에서 나온 결과이며, 이 앱이나 사용자의 파일에 대한 보장값이 아닙니다.

## 선택 기준

압축률, 손실, 인코딩 시간, 색상/알파/비트 심도, 출판 도구와의 호환성, Windows/Mac 배포 가능성을 함께 고려했습니다. 모든 이미지에 공통으로 최적인 코덱은 없습니다. 사진, 도표, 스캔, 벡터 문서에 같은 정책을 강제하지 않습니다.

## JPEG: Jpegli와 4:4:4 경로

[Google Jpegli](https://github.com/google/jpegli)는 기존 JPEG와 호환되는 인코더로 적응 양자화와 지각 기반 양자화 행렬 등을 사용합니다. [Google의 발표](https://opensource.googleblog.com/2024/04/introducing-jpegli-new-jpeg-coding-library.html)는 고품질 조건에서 전통 JPEG 대비 압축 효율 향상을 보고했습니다. [원 논문](https://arxiv.org/abs/2403.18589)은 고품질 사진 비교에서 더 낮은 비트레이트의 Jpegli에 대한 시각 선호를 조사했습니다. 이는 임의의 파일에 일률적인 절감률을 적용할 근거가 아닙니다.

현재 [pyjpegli](https://github.com/Maksim-Burtsev/pyjpegli) 0.3.2를 고정해 사용하며 이 바인딩은 RGB 입력과 품질만 받습니다. 실제 출력의 chroma sampling을 검사한 결과 4:2:0입니다. 따라서 가는 컬러 선과 색상 디테일을 우선하는 출판 사진 프리셋에는 Pillow의 libjpeg 경로를 **4:4:4**로 설정합니다. Jpegli는 균형·작은 파일 프리셋에서 사용합니다. JPEG에 DPI·ICC를 추가할 때는 APP 마커를 기록하며 재인코딩하지 않습니다.

[MozJPEG](https://github.com/mozilla/mozjpeg)는 trellis quantization과 progressive scan 최적화가 강점이지만 일반 libjpeg-turbo보다 인코딩이 느릴 수 있습니다. 이번 버전에서는 Jpegli와 표준 4:4:4 경로를 제공하고 별도 MozJPEG 런타임을 중복 배포하지 않습니다.

## PNG: OxiPNG

[OxiPNG](https://github.com/oxipng/oxipng)는 색상 유형·비트 심도·필터·DEFLATE를 최적화하는 무손실 도구입니다. 레벨이 높아도 항상 가장 작은 결과를 보장하는 것은 아닙니다. [pyoxipng API](https://github.com/nfrasser/pyoxipng)를 통해 메모리에서 레벨 2, 최대 5초 최적화를 적용하고 작은 결과만 선택합니다.

색상 palette 변환은 같은 픽셀을 표현할 때만 수행합니다. `optimize_alpha=False`로 투명 픽셀 안에 숨은 RGB를 보존합니다. `scale_16`과 손실 팔레트 양자화는 사용하지 않습니다. 원본 픽셀 수 유지 + PNG 출력은 RGBA 픽셀 비교로 검증합니다. 리샘플링을 선택한 경우에는 PNG 인코딩 자체가 무손실이어도 원본의 모든 디테일이 보존되는 것은 아닙니다.

## AVIF, WebP, JPEG XL

[Pillow의 포맷 문서](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)에 따라 AVIF와 WebP를 네이티브 인코더로 출력합니다. AVIF는 4:4:4와 speed 6, 최대 2개 인코더 스레드를 사용합니다. WebP는 method 6, 도표 모드에서는 lossless를 사용합니다. 두 형식 모두 ICC를 보존하고 EXIF 해상도를 기록합니다. 코덱별 같은 quality 숫자를 같은 시각 품질로 해석하지 않습니다.

[JPEG XL 참조 구현](https://github.com/libjxl/libjxl)은 차세대 손실/무손실 압축의 유력한 후보입니다. 하지만 PDF의 일반적인 이미지 필터로 그대로 삽입할 수 없고 출판 도구별 입력 지원 확인도 필요합니다. 이번 버전에는 JXL을 지원한다고 표시하지 않습니다. 도입 시에는 설치 크기·네이티브 배포와 실제 사용자 자료의 품질 비교를 별도로 검증해야 합니다.

## 리샘플링과 메모리

[Pillow Image.resize](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.resize)의 Lanczos, `reducing_gap=3.0`으로 큰 배율에서는 정수 축소를 먼저 수행합니다. 모든 후보를 원본 픽셀에서 다시 만들기 때문에 탐색 중 JPEG를 반복 재압축하지 않습니다. 확대하지 않습니다. 색상 프로파일을 보존하지만 현 경로는 gamma-encoded 공간에서 축소하며 선형광/HDR 리샘플러를 구현한 것은 아닙니다.

[libvips 축소 가이드](https://github.com/libvips/libvips/wiki/HOWTO----Image-shrinking)는 JPEG shrink-on-load와 순차 처리를 통한 속도·메모리 개선을 설명합니다. 초대형 TIFF/수백 MP 워크로드에서는 유력한 후속 엔진입니다. 현재 JPEG 미리보기는 Pillow의 `thumbnail`/`draft`를 통해 디코딩 단계부터 축소합니다. 원본 크기의 불필요한 복사본도 제거했습니다. PNG의 메타데이터는 `getexif()`가 전체 픽셀을 디코딩하는 경로를 피하고, 청크를 seek하며 읽습니다. 실제 압축에서는 대형 단일 이미지를 전체 디코딩하지만 배치 전체를 동시에 풀지는 않습니다.

## PDF: 구조와 사진을 구분

[pikepdf 이미지 문서](https://pikepdf.readthedocs.io/en/stable/topics/images.html)는 PDF 이미지가 여러 위치에서 재사용되고 색상 공간·마스크·Decode 등에 의해 해석된다고 설명합니다. 페이지를 비트맵으로 재출력하면 텍스트·벡터 정보가 없어지므로 그 방식은 사용하지 않습니다.

컨텐츠 스트림의 `q/Q`, `cm`, `Do`와 중첩 Form의 Matrix를 추적해 배치 크기를 구합니다. 공유 이미지는 가장 큰 배치에 필요한 해상도를 기준으로 한 번만 변경합니다. 알 수 없는 구조·특수 색상·마스크 이미지는 건너뜁니다. 패턴, Type3 폰트, 주석 appearance에 연결된 이미지도 보호합니다. 8비트 RGB/Gray의 DCT/Flate 이미지 중 적합한 것만 리샘플링합니다. 낮은 색상 수의 그래픽은 Flate를 유지합니다. 변경한 이미지 스트림이 커지면 원본 스트림을 유지하고 DPI 예외를 표시합니다.

[pikepdf 저장 API](https://pikepdf.readthedocs.io/en/stable/api/main.html)로 스트림 재압축과 object stream 생성을 적용합니다. 기본적으로 전체 문서의 텍스트·폰트·벡터·메타데이터를 유지합니다. 결과가 더 커지고 이미지 변경도 없다면 원본을 유지합니다. 별도의 PDF/A/X 적합성 인증은 수행하지 않습니다.

미리보기에는 [pypdfium2](https://pypdfium2.readthedocs.io/en/stable/python_api.html)를 사용합니다. PDFium은 스레드 안전하지 않으므로 GUI 작업자 사이에서도 전역 잠금으로 한 번에 한 작업만 수행합니다. 이미지 병합은 img2pdf로 무손실 삽입 후 최종 PDF에서 필요한 이미지에 한 번만 손실 압축합니다.

## 최대 용량과 DPI 예측

1. 즉시 표시는 원본 파일 크기와 면적 비율에 기초한 거친 추정이며 해당 문구를 표시합니다. 콘텐츠 복잡도를 알기 전의 계산입니다.
2. 자동 작업은 작은 원본 미리보기까지입니다. `정확히 계산`을 눌렀을 때 별도 프로세스에서 전체 시험 인코딩을 합니다. PDF 병합 모드에서는 전체 결과를 시험 생성합니다. 설정 변경 시 진행 중인 시험 프로세스를 취소합니다.
3. 원본 픽셀 수와 높은 품질에서 시작합니다. 상한을 초과하면 품질 하한에서 가능한 해상도를 이분 탐색하고, 남은 공간에서 품질을 높입니다. 용량 모드에는 DPI 하한이 없으며 DPI 모드의 값이나 프리셋 해상도를 참조하지 않습니다. 같은 형식의 원본이 이미 상한 이내라면 디코딩 없이 원본을 유지합니다. PNG 용량 탐색에서는 OxiPNG를 매 후보마다 반복하지 않고 선택한 결과에 한 번 적용합니다.
4. 저장하기 전에 실제 바이트 수를 검사합니다. 최소 1픽셀까지 줄여도 헤더·프로파일 또는 PDF 보존 요소 때문에 상한을 초과하면 그 출력은 저장하지 않습니다.
5. 알려진 인쇄 크기에 결과 픽셀 수를 나눠 유효 DPI를 표시합니다. PDF는 측정 가능한 배치의 최저·최고 DPI를 표시합니다.

정해진 횟수로 후보를 탐색하므로 전역 최적값을 증명하지 않습니다. 인코더 크기가 완벽히 단조적이지 않아 가장 높은 가능한 설정을 건너뛸 수 있지만 저장하는 결과의 바이트 상한은 직접 검사합니다.

## 검증 범위

합성 사진·도표, 알파, ICC, EXIF 방향, 서로 다른 DPI 축, 여러 프레임 TIFF, HEIC/AVIF 입출력, 크기 제한 성공/실패, 취소·이름 충돌, 텍스트가 포함된 PDF, 중첩 Form, 공유 회전 이미지, 마스크 보존, 상속 리소스, GUI 시험 압축·실제 저장을 테스트합니다. 이는 모든 출판 자료의 시각 품질에 대한 벤치마크를 대체하지 않습니다. 실제 원고에서 원본과 출력물을 대조해 선택한 프리셋을 검증하는 것이 필요합니다.
