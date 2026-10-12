# 新大阪イベントナビ 自動収集スターター（iPhoneのみ）

## 重要な制限
- この仕組みは公式ページに `schema.org/Event` のJSON-LDがある場合にイベントを自動抽出します。ページが変わっただけの場合は「ページ変更」と通知し、中止・日程変更とは断定しません。
- 会場サイトの構造が変わったり、イベント情報が画像やJavaScriptでしか提供されない場合、自動抽出できないことがあります。実際の運用前に監視対象ごとの検証が必要です。
- 初期設定は6時間ごと。GitHub Actionsのスケジュールは混雑時に遅れることがあります。
- GitHub Pagesは静的ホスティングです。Actionsが生成したJSONをリポジトリにコミットすることで公開します。

## iPhoneでの導入
1. GitHubリポジトリ `w7k5w5r9rt-alt/shinosaka-event-navi` をSafariで開く。
2. 既存のv3 HTMLとイベントJSONを先にダウンロードしてバックアップする。
3. このZIPのファイルをリポジトリに追加する。フォルダ構成を保つ：
   - `shinosaka_event_navi_v4.html`（新しいアプリ画面）
   - `sources.json`
   - `collector.py`
   - `requirements.txt`
   - `.github/workflows/collect-events.yml`
   - `data/` 内のJSONは初回実行時に作られるので空でもよい
4. GitHubの「Add file」→「Upload files」で追加し、Commit changes。
5. リポジトリの「Actions」タブ → `Collect event updates` → `Run workflow` → `Run workflow` をタップ。
6. 実行が緑のチェックになったら、リポジトリの `data` フォルダに `auto-events.json`、`change-alerts.json`、`collector-status.json` が作成されたか確認。
7. `data` が公開されていることを確認してから、以下を開く：
   `https://w7k5w5r9rt-alt.github.io/shinosaka-event-navi/shinosaka_event_navi_v4.html`
8. 「データ管理」→「今すぐ更新」をタップ。取得状態と変更候補が表示されれば接続完了。
9. 以後は6時間ごとにActionsが実行され、更新されたJSONが公開される。アプリ内表示は「今すぐ更新」を押すと最新化される。常時プッシュ通知ではない。

## iPhoneからファイルを追加する方法
GitHubのリポジトリ画面で `Add file` → `Upload files` を使います。ZIPそのものをアップロードするのではなく、FilesアプリでZIPを展開し、中のファイルをアップロードしてください。iPhoneのGitHub画面でフォルダ構成を作りにくい場合は、ファイル名入力欄に `.github/workflows/collect-events.yml` のようにパスを指定してファイルを個別作成する方法もあります。

## 注意
- `sources.json` のURLは最初の監視候補です。ページ構造は変わり得ます。収集結果が0件でも自動処理は動いている可能性があるため、`data/collector-status.json` の `structuredEventsExtractedThisRun` を確認してください。
- GitHub ActionsがデータJSONをコミットするため、リポジトリ設定のActions権限が必要です。失敗時は「Settings」→「Actions」→「General」→「Workflow permissions」で書き込み権限を確認してください。
- GitHub Pagesが `main` ブランチのルートから公開されている前提です。
- 自動取得は完璧な網羅を保証しません。宿泊料金の変更判断前に公式発表を確認してください。
