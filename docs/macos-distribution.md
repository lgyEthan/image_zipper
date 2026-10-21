# Mac 서명과 Apple 공증

GitHub로 배포하는 Mac 앱은 **Developer ID Application** 인증서로 서명합니다. `Apple Development`나 `Apple Distribution` 인증서로 대체하지 않습니다. ZIP 배포에는 `Developer ID Installer`가 필요하지 않습니다.

## 이 Mac에서 한 번 준비할 항목

1. Xcode → Settings → Apple Accounts에서 Apple Developer Program 계정을 선택합니다.
2. Manage Certificates의 `+`에서 **Developer ID Application** 인증서를 생성합니다. 다른 Mac에 이미 인증서와 개인 키가 있다면 그 서명 ID를 가져올 수도 있습니다. 기존 인증서를 취소할 필요는 없습니다.
3. 터미널에서 `security find-identity -v -p codesigning`을 실행해 유효한 `Developer ID Application: …` 항목을 확인합니다.
4. 공증용 계정 정보를 로컬 키체인에 등록합니다.

```bash
xcrun notarytool store-credentials image-zipper
```

명령이 요구하는 Apple Account, Team ID, 앱 전용 암호를 터미널의 대화형 입력으로 제공합니다. 앱 전용 암호나 개인 키를 저장소·채팅에 넣지 않습니다. 이미 공증용 키체인 프로필이 있다면 그 이름을 그대로 사용합니다. App Store Connect API 키를 사용하는 계정도 `notarytool`이 지원합니다.

## 서명된 빌드

```bash
.venv/bin/python -I scripts/build.py \
  --codesign-identity 'Developer ID Application: YOUR NAME (TEAMID)'
```

인증서 이름 대신 `security find-identity`가 표시한 SHA-1 식별자를 사용할 수 있습니다. 빌드는 유효한 Developer ID Application 인증서인지 먼저 확인하고, PyInstaller로 앱·내부 실행 파일·라이브러리를 서명합니다. Hardened Runtime과 타임스탬프를 적용하고, 서명 검증 및 완성 앱의 3회 실행 검사를 수행합니다. 개인 키를 파일로 내보내지 않습니다.

서명만 한 ZIP에는 `-signed.zip`을 붙여 공증 완료본과 구분합니다. 공증은 다음 단계에서 별도로 확인해야 합니다. 플래그를 생략한 기존 개발 빌드 동작은 같습니다.

## 공증 후 배포

```bash
# 실제 빌드에서 생성된 -signed.zip 경로를 지정합니다.
xcrun notarytool submit dist/ImageZipper-v0.1.2-macOS-arm64-signed.zip \
  --keychain-profile image-zipper --wait
```

Apple이 반환한 제출 ID를 기록하고 **Accepted** 상태를 확인합니다. 응답이 끊기거나 대기 시간이 끝나면 같은 제출 ID로 `xcrun notarytool info ID --keychain-profile image-zipper`를 실행합니다. 거절된 경우 `xcrun notarytool log ID --keychain-profile image-zipper`로 원인을 확인합니다.

Accepted 이후 앱에 공증 티켓을 부착하고 확인합니다.

```bash
xcrun stapler staple dist/ImageZipper.app
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

Intel 빌드에도 별도로 서명·공증·티켓 검증을 적용합니다. 위 `arm64` 경로는 실제 플랫폼에 맞게 바꿉니다. 성공한 공증·Gatekeeper 검증을 확인하기 전에는 기존 공개 릴리스를 공증 완료로 표시하지 않습니다.

공식 문서: [Developer ID 인증서](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/), [Apple 공증 절차](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow), [PyInstaller 서명](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).
