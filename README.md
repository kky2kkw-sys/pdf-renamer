# PDF Renamer

DOI 기반 논문 PDF 파일 자동 정리 도구.

다운로드한 논문 PDF의 의미 없는 파일명을 `연도_저자_저널약어_제목.pdf` 형태로 자동 변환합니다.

## Features

- **DOI 자동 추출** — PDF 첫 페이지에서 DOI를 자동으로 찾아 CrossRef API로 메타데이터 조회
- **파일명 자동 생성** — NLM 표준 저널 약어 사용, 커스터마이즈 가능한 패턴
- **DOI 수동 입력** — 자동 추출 실패 시 더블클릭 또는 우클릭으로 직접 입력
- **중복 DOI 감지** — 같은 논문이 다른 이름으로 중복 저장된 경우 알림
- **Undo** — 이름 변경 후 원래 파일명으로 복원 가능
- **드래그 앤 드롭** — 파일이나 폴더를 창에 끌어다 놓기
- **패턴 설정** — 컴포넌트 순서, 제목 단어 수, 최대 길이 등 커스터마이즈

## Example

```
Before:  1-s2.0-S0168827825006311-main.pdf
After:   2026_Reig_J Hepatol_BCLC Staging Update Treatment.pdf
```

## Installation

### Python으로 실행

```bash
pip install -r requirements.txt
python pdf_renamer.py
```

### 독립 실행 파일 (.exe) 빌드

```bash
pip install pyinstaller
pip install -r requirements.txt
pyinstaller --onefile --windowed --name PDF_Renamer pdf_renamer.py
```

`dist/PDF_Renamer.exe`가 생성됩니다. Python 없이 실행 가능.

## Usage

1. 폴더 선택 (또는 드래그 앤 드롭)
2. **스캔** → PDF 목록 로드
3. **메타데이터 조회** → DOI 추출 + CrossRef 자동 조회
4. 결과 확인 (새 파일명 더블클릭으로 수동 편집 가능)
5. **이름 변경 실행**

DOI를 못 찾은 파일은 DOI 컬럼 더블클릭 또는 우클릭 → "DOI 수동 입력"으로 처리.

## Configuration

메뉴 > 설정 > 파일명 패턴 설정에서 조정 가능. 설정은 `pdf_renamer_config.json`에 저장됩니다.

사용 가능한 패턴 변수: `{year}`, `{author}`, `{journal}`, `{title}`

기본 패턴: `{year}_{author}_{journal}_{title}`

## License

MIT
