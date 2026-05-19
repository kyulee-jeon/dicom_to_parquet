# DICOM 헤더 추출 데모 (한국어)

이 폴더에는 `dicom_to_parquet.py`를 사용해 DICOM 헤더 메타데이터를 추출하는 세 가지 출력 방식에 대한 데모 노트북이 포함되어 있습니다.

## 데모 노트북

| 언어 | 노트북 |
|------|--------|
| 영어 | [dicom_to_parquet_demo.ipynb](dicom_to_parquet_demo.ipynb) |
| 한국어 | [dicom_to_parquet_demo-kor.ipynb](dicom_to_parquet_demo-kor.ipynb) |

## 세 가지 추출 방법

| # | 방법 | 함수 | 출력 형식 |
|---|------|------|-----------|
| 1 | **Parquet만 저장** | `build_parquet()` | Hive 파티셔닝 Parquet (`modality=.../tag=.../part-b0-0.parquet`) |
| 2 | **Parquet → CSV 변환** | `build_parquet(..., export_csv=...)` | Parquet 파티션 + 통합 `all_tags.csv` |
| 3 | **CSV만 저장** | `build_csv_direct()` | 단일 평면 `all_tags.csv` (Parquet 없음) |

세 가지 방법 모두 동일한 행을 생성합니다. 방법 선택은 사용하는 도구와 데이터셋 크기에 따라 결정하면 됩니다.

## 샘플 실행 결과

데모 노트북은 **MIMIC-IV Echo DICOM 파일 5개** (모달리티: US)를 대상으로 실행되었습니다.  
세 가지 방법 각각의 사전 생성 결과는 아래 경로에서 확인할 수 있습니다:

```
files/mimic-iv-echo-sample_headers/
├── 01_parquet_only/          # 방법 1 결과 — Hive 파티셔닝 Parquet
├── 02_parquet_and_csv/       # 방법 2 결과 — Parquet + all_tags.csv
└── 03_csv_only/              # 방법 3 결과 — 단일 all_tags.csv
```

샘플 실행 요약:

| 항목 | 값 |
|------|----|
| 처리된 DICOM 파일 수 | 5개 |
| 추출된 전체 행 수 | 351행 |
| 고유 DICOM 태그 수 | 79개 |
| 모달리티 | US |
