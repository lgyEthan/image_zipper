# Mac 서명과 Apple 공증

GitHub로 배포하는 Mac 앱은 **Developer ID Application** 인증서로 서명합니다. `Apple Development`나 `Apple Distribution` 인증서로 대체하지 않습니다. ZIP 배포에는 `Developer ID Installer`가 필요하지 않습니다.

## 이 Mac에서 한 번 준비할 항목

1. Xcode → Settings → Apple Accounts에서 Apple Developer Program 계정을 선택합니다.
2. Manage Certificates의 `+`에서 **Developer ID Application** 인증서를 생성합니다. 다른 Mac에 이미 인증서와 개인 키가 있다면 그 서명 ID를 가져올 수도 있습니다. 기존 인증서를 취소할 필요는 없습니다.
3. 터미널에서 `security find-identity -v -p codesigning`을 실행해 유효한 `Developer ID Application: …` 항목을 확인합니다.

서명 중 macOS가 키체인 접근 승인을 요구할 수 있습니다. 로그인된 Xcode 계정으로 아래 공증 경로를 사용하면 별도 앱 전용 암호를 만들 필요가 없습니다. 개인 키를 파일로 내보내거나 CI에 복사하지 않습니다.

## 서명된 빌드

```bash
.venv/bin/python -I scripts/build.py \
  --codesign-identity 'Developer ID Application: YOUR NAME (TEAMID)'
```

인증서 이름 대신 `security find-identity`가 표시한 SHA-1 식별자를 사용할 수 있습니다. 빌드는 유효한 Developer ID Application 인증서인지 먼저 확인하고, PyInstaller로 앱·내부 실행 파일·라이브러리를 서명합니다. Hardened Runtime과 타임스탬프를 적용하고, 서명 검증 및 완성 앱의 3회 실행 검사를 수행합니다. 개인 키를 파일로 내보내지 않습니다.

서명만 한 ZIP에는 `-signed.zip`을 붙여 공증 완료본과 구분합니다. 공증은 다음 단계에서 별도로 확인해야 합니다. 플래그를 생략한 기존 개발 빌드 동작은 같습니다.

## 로그인된 Xcode 계정으로 공증

서명된 앱으로 Xcode 배포 아카이브를 만듭니다. 이 스크립트는 앱 안의 모든 Mach-O 파일에 Developer ID, 같은 Team ID, 보안 타임스탬프, Hardened Runtime이 적용됐는지 검사한 뒤 아카이브와 제출 옵션을 만듭니다. 기존 아카이브는 덮어쓰지 않습니다.

```bash
.venv/bin/python -I scripts/prepare_macos_distribution.py \
  --app dist/ImageZipper.app \
  --archive artifacts/signing-macos/ImageZipper-arm64.xcarchive \
  --identity 'Developer ID Application: YOUR NAME (TEAMID)'

xcodebuild -exportArchive \
  -archivePath artifacts/signing-macos/ImageZipper-arm64.xcarchive \
  -exportOptionsPlist artifacts/signing-macos/ImageZipper-arm64.export-options.plist \
  -allowProvisioningUpdates
```

옵션은 `developer-id`, `destination=upload`, 수동 인증서 선택으로 고정됩니다. 이 명령의 `EXPORT SUCCEEDED`는 **제출 성공**이며 공증 승인 자체는 아닙니다.

제출한 `.xcarchive`를 Xcode로 열고 Organizer의 **Show Status Log**에서 **Ready to distribute**를 확인합니다. Xcode가 파일 미리보기로 열었다면 **Open with Xcode**를 누릅니다. Organizer는 아카이브를 관리 폴더로 가져오므로, 원래 경로의 상태 파일과 화면의 상태가 다를 수 있습니다. 승인된 항목에서 **Export Notarized App / Export App…**으로 공증 티켓이 포함된 앱을 내보냅니다. 다시 제출할 필요는 없습니다.

Intel CI 앱은 ZIP 체크섬을 확인한 뒤 별도 작업 폴더에 풀고 같은 스크립트에 `--resign`을 추가합니다. 실행 파일과 라이브러리, 프레임워크, 최상위 앱 순서로 서명합니다. 이 옵션은 지정한 앱을 수정하므로 원본 ZIP을 보관하고 복사본에 사용합니다. 아카이브 이름도 `ImageZipper-Intel.xcarchive`로 구분합니다. 서명 후 Intel Mac 또는 Rosetta에서 `--self-test`를 실행하고 각각 공증을 제출합니다.

## notarytool을 사용하는 대안

자동화 환경에서 `notarytool`을 쓰려면 공증용 계정 정보를 로컬 키체인에 등록합니다.

```bash
xcrun notarytool store-credentials image-zipper
```

명령이 요구하는 Apple Account, Team ID, 앱 전용 암호를 터미널의 대화형 입력으로 제공합니다. 앱 전용 암호나 개인 키를 저장소·채팅에 넣지 않습니다. 기존 공증 프로필이나 App Store Connect API 키도 사용할 수 있습니다.

```bash
# 실제 빌드에서 생성된 -signed.zip 경로를 지정합니다.
xcrun notarytool submit dist/ImageZipper-v0.1.2-macOS-arm64-signed.zip \
  --keychain-profile image-zipper --wait
```

Apple이 반환한 제출 ID를 기록하고 **Accepted** 상태를 확인합니다. 응답이 끊기거나 대기 시간이 끝나면 같은 제출 ID로 `xcrun notarytool info ID --keychain-profile image-zipper`를 실행합니다. 거절된 경우 `xcrun notarytool log ID --keychain-profile image-zipper`로 원인을 확인합니다.

Accepted 이후 앱에 공증 티켓을 부착하고 확인합니다.

```bash
xcrun stapler staple dist/ImageZipper.app
```

## 배포 직전 검증

Xcode에서 내보낸 앱에는 티켓이 이미 부착되어 있습니다. 아래 경로는 실제 공증된 앱 경로로 바꿉니다. 세 명령이 모두 성공하고 Gatekeeper 결과가 `source=Notarized Developer ID`인지 확인합니다.

```bash
xcrun stapler validate dist/ImageZipper.app
codesign --verify --deep --strict dist/ImageZipper.app
spctl --assess --type execute --verbose=4 dist/ImageZipper.app
```

티켓을 붙인 앱으로 **새 ZIP을 만든 뒤** SHA-256을 다시 계산합니다. 공증에 제출했던 ZIP을 그대로 배포하면 로컬에 부착한 티켓이 포함되지 않습니다.

```bash
ditto -c -k --sequesterRsrc --keepParent dist/ImageZipper.app \
  dist/ImageZipper-v0.1.2-macOS-arm64-notarized.zip
cd dist
shasum -a 256 ImageZipper-v0.1.2-macOS-arm64-notarized.zip \
  > ImageZipper-v0.1.2-macOS-arm64-notarized.zip.sha256
```

생성한 ZIP을 새 폴더에 풀고 티켓·서명·Gatekeeper 검사를 다시 수행합니다. 압축을 푼 앱의 `Contents/MacOS/ImageZipper --self-test report.json`을 실행해 네이티브 코덱과 GUI도 검사합니다. 검증 보고서와 SHA-256을 ZIP과 함께 배포합니다.

Intel 빌드에도 별도로 서명·공증·티켓 검증을 적용합니다. 위 `arm64` 경로는 실제 플랫폼에 맞게 바꿉니다. 성공한 공증·Gatekeeper 검증을 확인하기 전에는 기존 공개 릴리스를 공증 완료로 표시하지 않습니다.

공식 문서: [Developer ID 인증서](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/), [Apple 공증 절차](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow), [PyInstaller 서명](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).
