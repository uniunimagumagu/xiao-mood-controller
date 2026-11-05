# XIAO Mood Controller

作業開始時の「何を聴くか」選択を高速化するハンドセット。
Seeed **XIAO nRF52840 Plus（1.47" タッチディスプレイ搭載版）** を手元の選択リモコンとして使い、
PC上の **Brave** で YouTube を再生する。

- 縦スワイプ = ジャンル切替 （J-POP / ゲーム実況 / 雑談 / ラジオ / Lo-fi / 環境音）
- 横スワイプ = ジャンル内の別の曲（おすすめレーンあり）
- タップ = その場でBrave再生（シングルタブ・タブ増殖なし）
- 両端ダブルタップ = ±1分シーク（YouTube機能）
- 1.5秒長押し = ChatGPTモード（PC側のターミナルから質問・応答を装置に表示）
- 候補は公式チャンネルRSS（APIキー無し）またはYouTube Data API（キーあり）から最新を取得。
  スワイプ先はPCが先に「用意」しており、端末はローカルで即座に画面切替する。

`data/mood_daemon.log` に全動作が流れる。デバッグは `tail -f data/mood_daemon.log`。

---

## 1. 実行に必要なもの

| 項目 | 用途 | 備考 |
|---|---|---|
| XIAO nRF52840 Plus (1.47" Touch) | ジェスチャーデバイス | USB-CでPCへ |
| PC (Windows 10/11 or macOS 13+) | daemon＋再生 | Python 3.10+ |
| Brave Browser | YouTube再生 | 既定インストールパス対応 |
| arduino-cli | ファーム書込 | brew / scoop / choco / MSI |
| git | Seeed_GFX2 入手 | クローンに使用 |

オプションAPIキー: `YOUTUBE_API_KEY`（Data API検索）と `OPENAI_API_KEY`（ChatGPTモード）。無くても動く。

## 2. ディレクトリ構成

```
xiao-mood-controller/
├── README.md                     ← 本文
├── requirements.txt
├── config/
│   ├── categories.yaml           ← ジャンル定義（検索クエリ・RSS・固定候補）
│   └── env.example               ← .envの雛形（APIキー等）
├── server/                       ← PC側 daemon
│   ├── handset.py                ← メイン: シリアル受信→状態→Brave→画面データ
│   ├── brave.py                  ← Brave/CDP制御・oEmbed・シーク
│   ├── youtube_client.py         ← 候補収集（RSS/Data API/固定リスト）
│   ├── state.py                  ← 画面の状態機械
│   ├── config.py                 ← YAML読込
│   ├── env_loader.py             ← .env読み（Windowsでも.coderc不要）
│   ├── http_api.py               ← FastAPI管理API（任意）
│   └── ui_preview.py             ← PC上でUIイメージをPNGにする（開発用）
├── firmware/
│   └── xiao_mood_handset/        ← デバイス側スケッチ
│       └── xiao_mood_handset.ino
├── tools/
│   ├── build_all.py              ← 一気通貫セットアップ／コンパイル／書込
│   ├── supervisor.py             ← daemon死活監視（tmux不要）
│   ├── patch_seeed_gfx.py        ← Seeed_GFX2のmbed高速転送パッチ
│   ├── setup_env.sh              ← mac/Linux用ワンショット
│   ├── setup_env.ps1             ← Windows用ワンショット
│   └── setup_env.bat             ← Windows cmdラッパ
└── data/                         ← 実行時に生成（キャッシュ・ログなど）
```

## 3. セットアップ手順（macOS / Linux）

```bash
cd xiao-mood-controller
tools/setup_env.sh                # venv作成→依存→arduino-cli設定→Seeed_GFX2パッチ→コンパイル
```

必要なインストール先が無い場合: `brew install arduino-cli`、`git`は確実に。

## 4. セットアップ手順（Windows）

PowerShellで:

```powershell
cd xiao-mood-controller
powershell -ExecutionPolicy Bypass -File tools\setup_env.bat
```

必要なツール: `> choco install arduino-cli`（または scoopインストールMSI）。
Braveは既定インストールパスを自動検出（Program Files / LocalAppDataのどちらか）。

## 5. ファームウェア書き込み

デバイスをUSB-Cで接続してから:

```bash
.venv/bin/python tools/build_all.py --upload
```

Windows:
```powershell
.venv\Scripts\python tools\build_all.py --upload
```

ポートの自動検出（XIAO / nRF52840という説明文字を伴うポートを優先）。明示指定は
`--port COM5`（Windows）や `--port /dev/cu.usbmodem1301`（mac）。

## 6. 実行（毎日使うコマンド）

macOS / Linux:
```bash
.venv/bin/python tools/supervisor.py     # daemon死活監視ループ
```

Windows:
```powershell
.venv\Scripts\python tools\supervisor.py
```

daemonは `data/mood_daemon.log` に書き続ける。ログ監視:
```bash
tail -f data/mood_daemon.log
```

PC再起動後も `tools/supervisor.py` を立ち直せばよいだけ。

## 7. 操作仕様

| 操作 | 挙動 |
|---|---|
| 上スワイプ | 次のジャンル（ブラウザも即切替） |
| 下スワイプ | 前のジャンル |
| 右スワイプ | 次の候補（チャンネル新着レーン） |
| 左スワイプ | 前の候補（こちら用意の固定/プールレーン） |
| 中央タップ | 表示中の曲をBraveで再生 |
| 左端→右端ダブルタップ | ±1分シーク（YouTube前提） |
| 1.5秒長押し | ChatGPTモード切替 |

実際の再生が本体で始まるまでは1〜2秒（YouTubeの読み込み待ち）。画面表示は即切替。

## 8. ChatGPTモード利用

1. `.env` に `OPENAI_API_KEY=sk-...` を書く（`config/env.example` 参照）。
2. デバイスを1.5秒長押しでChatモードに切替:
   - daemonがPC側の標準入力を待つ
3. `tmux attach -t mood`（またはsupervisorを直接実行している端末）で質問をタイプ。
   → OpenAI APIが応答し、端末に表示する。

## 9. HTTP管理API（任意）

```bash
.venv/bin/uvicorn server.http_api:app --host 127.0.0.1 --port 8765
```

- `GET /health` `GET /categories` `GET /categories/{id}/candidates` `POST /play` `POST /refresh/{id}`
- ローカル限定（外部公開するなら `.env` で `MOOD_TOKEN=` 設定して `X-Mood-Token` ヘッダ）

## 10. ジャンル・候補カスタマイズ

`config/categories.yaml`:

- `queries` — タイトル検索クエリ群（APIキーがある場合のみ）
- `favorite_channels` — 公式RSS配信元のチャンネルID (`UC...`)。**キー無しでも候補自動生成**
- `fallback` — 固定URL候補（`"ID|タイトル|チャンネル"`）
- `order`/`published_after_days` — API検索時の「最近の人気MV」を抽出

## 11. トラブルシューティング

| 症状 | 対応 |
|---|---|
| 画面が暗い | バックライトは `analogWrite(BL=37, 160)` で点灯。再フラッシュ＋给宝宝 power-cycle |
| 切替が遅い（>1秒） | `tools/patch_seeed_gfx.py` の適用を見直す（Seeed_GFX2更新で消え易い） |
| デバイスが応答しない | デーモンをkill→挿し直し→再書込 (`tools/build_all.py --upload`) |
| ラジオ回が最近の新着のみ | ラジオ243チャンネルが投稿していない時。fallback固定候補に差し替え |
| Braveタブが乱発 | CDP 9222が遊んでない時。mood私は「プロファイル」で1タブ固定管理 |
| WindowsでCOMポートが出ない | XIAO CDCドライバ入れる／他のUSBケーブル（通電専用は通信専用ケーブル）|

### mac / Linux USB サスペンド問題

daemonが動いている間はシリアルから完全切離さない（suspendを起こさない）。
故障時は `tools/build_all.py --upload` で自動再フラッシュする。

## 12. 既知の制限

- 日本語グリフはu8g2の `unifont_t_japanese1/2/3` 収録分（約2900字）。稀な漢字は `?`
- ライブ配信はサムネイルが取れないため端末では表示しない（現仕様では非表示）
- Brave Shieldsの仕様により広告なしは保証されない（規約上の設計あるいは著作権）
- `server/thumbs.py`（旧サムネイル実装）は未使用として保管。将来の実装用

## 13. ディレクトリ内の重要な値

| 変数 | 既定 | 説明 |
|---|---|---|
| `MOOD_CDP_PORT` | 9222 | Braveデバッグポート（localhost のみ） |
| `MOOD_PAGE_PORT` | 8642 | 端末内蔵panelページのローカルホスト配信ポート |
| `MOOD_TOKEN` | (未設定) | HTTP APIをトークンで保護 |

---

## 貢献： 開発コマンド一覧

```bash
.venv/bin/python -m pytest tests/ -q                     # ユニットテスト
.venv/bin/python tools/build_all.py --skip-deps          # ファーム再コンパイル
.venv/bin/python tools/build_all.py --upload             # 再書込
tail -f data/mood_daemon.log                             # 全動作ログ
```

バグ報告・機能提案はんでissueへ。自分で直せる時は.debug命令と一緒にlogから診断できます。
