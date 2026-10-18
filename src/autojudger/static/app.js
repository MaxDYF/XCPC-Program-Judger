const $ = (id) => document.getElementById(id);
const form = $('evaluation-form');
const submitButton = $('submit-button');
const progressPanel = $('progress-panel');
const errorPanel = $('error-panel');
const resultPanel = $('result-panel');
const phases = ['analyzing', 'clarity', 'searching', 'collecting', 'ranking'];
let currentReport = null;
const activeJobKey = 'xcpc-active-job';

function bindFile(inputId, zoneId, nameId) {
  const input = $(inputId);
  const zone = $(zoneId);
  const name = $(nameId);
  const refresh = () => {
    const file = input.files?.[0];
    zone.classList.toggle('selected', Boolean(file));
    if (file) name.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
  };
  input.addEventListener('change', refresh);
  for (const eventName of ['dragenter', 'dragover']) {
    zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.add('dragging'); });
  }
  for (const eventName of ['dragleave', 'drop']) {
    zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.remove('dragging'); });
  }
  zone.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    refresh();
  });
}
bindFile('statement_pdf', 'pdf-zone', 'pdf-name');
bindFile('std_file', 'std-zone', 'std-name');

const count = $('reference_count');
count.addEventListener('input', () => { $('reference-output').textContent = `${count.value} 道`; });
$('retry-button').addEventListener('click', () => { errorPanel.classList.add('hidden'); form.scrollIntoView({ behavior: 'smooth', block: 'center' }); });

function showError(message) {
  progressPanel.classList.add('hidden');
  resultPanel.classList.add('hidden');
  $('error-text').textContent = message;
  errorPanel.classList.remove('hidden');
  errorPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function updateProgress(job) {
  const phase = job.phase;
  const index = phases.indexOf(phase);
  const descriptions = {
    uploading: '正在上传题面 PDF 和标准程序，上传完成后会开始评测。',
    queued: '任务已创建，等待处理。',
    analyzing: '正在阅读题面和标准程序，推断算法与相关标签。',
    clarity: '正在还原去背景化的题意，并评估原题面是否容易理解。',
    searching: '正在 Codeforces 题库中建立难度梯度。',
    collecting: `正在读取题面与官方题解代码：${job.completed} / ${job.total} 道。`,
    ranking: `正在进行匿名排序：第 ${job.completed} / ${job.total} 轮完成。`
  };
  $('progress-detail').textContent = descriptions[phase] || '正在处理…';
  const value = phase === 'collecting' ? 30 + 47 * job.completed / Math.max(1, job.total)
    : phase === 'ranking' ? 80 + 18 * job.completed / Math.max(1, job.total)
    : phase === 'searching' ? 27 : phase === 'clarity' ? 18 : phase === 'analyzing' ? 10 : 3;
  $('progress-fill').style.width = `${value}%`;
  document.querySelectorAll('.progress-stages span').forEach((element, itemIndex) => {
    element.classList.toggle('current', itemIndex === index);
    element.classList.toggle('done', itemIndex < index);
  });
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function awaitJob(jobId) {
  while (true) {
    let response;
    try {
      response = await fetch(`/jobs/${encodeURIComponent(jobId)}`, {
        cache: 'no-store', signal: AbortSignal.timeout(20000)
      });
    } catch {
      throw new Error('读取任务状态超时。评测可能仍在后台进行，请到“任务与历史”查看。');
    }
    if (response.status === 404) {
      const history = await fetch(`/api/history/${encodeURIComponent(jobId)}`, {
        cache: 'no-store', signal: AbortSignal.timeout(20000)
      });
      if (history.ok) {
        sessionStorage.removeItem(activeJobKey);
        return (await history.json()).report;
      }
    }
    if (!response.ok) throw new Error('无法读取任务状态，请到“任务与历史”查看。');
    const job = await response.json();
    if (job.status === 'completed') {
      sessionStorage.removeItem(activeJobKey);
      return job.result;
    }
    if (job.status === 'failed') {
      sessionStorage.removeItem(activeJobKey);
      throw new Error(job.error || '评估未完成');
    }
    updateProgress(job);
    await delay(1200);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const pdf = $('statement_pdf').files?.[0];
  const std = $('std_file').files?.[0];
  if (!pdf || !std) return showError('请同时选择题面 PDF 和标准程序。');
  if (!pdf.name.toLowerCase().endsWith('.pdf') || pdf.size > 20 * 1024 * 1024) return showError('题面必须是最多 20 MB 的 PDF，且不得超过 10 页。');
  if (std.size > 1024 * 1024) return showError('标准程序不能超过 1 MB。');
  submitButton.disabled = true;
  errorPanel.classList.add('hidden');
  resultPanel.classList.add('hidden');
  progressPanel.classList.remove('hidden');
  updateProgress({ phase: 'uploading', completed: 0, total: 0 });
  progressPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
  try {
    const response = await fetch('/jobs', { method: 'POST', headers: { 'X-XCPC-ProblemJudger-Request': '1' },
      body: new FormData(form), signal: AbortSignal.timeout(120000) });
    const job = await response.json();
    if (!response.ok) throw new Error(typeof job.detail === 'string' ? job.detail : '提交失败');
    sessionStorage.setItem(activeJobKey, job.job_id);
    updateProgress({ phase: 'queued', completed: 0, total: 0 });
    const report = await awaitJob(job.job_id);
    showReport(report);
  } catch (error) {
    showError(error.name === 'TimeoutError'
      ? '上传超时。服务端可能仍在处理，请到“任务与历史”查看。'
      : error.message || '评估未完成，请重试。');
  } finally {
    submitButton.disabled = false;
  }
});

async function resumeJob() {
  const jobId = sessionStorage.getItem(activeJobKey);
  if (!jobId) return;
  submitButton.disabled = true;
  progressPanel.classList.remove('hidden');
  updateProgress({ phase: 'queued', completed: 0, total: 0 });
  try {
    showReport(await awaitJob(jobId));
  } catch (error) {
    if (String(error.message).includes('无法读取任务状态')) {
      sessionStorage.removeItem(activeJobKey);
    }
    showError(error.message || '无法恢复任务状态。');
  } finally {
    submitButton.disabled = false;
  }
}
resumeJob();

async function updateSidebarJobs() {
  try {
    const response = await fetch('/api/jobs', { cache: 'no-store' });
    if (!response.ok) return;
    const data = await response.json();
    const badge = $('sidebar-live-count');
    badge.textContent = String(data.total);
    badge.classList.toggle('hidden', data.total === 0);
  } catch { /* A transient status error should not interrupt the form. */ }
}
updateSidebarJobs();
setInterval(updateSidebarJobs, 4000);

function makeLink(url, label) {
  const link = document.createElement('a');
  link.href = url;
  link.textContent = label;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function showReport(data) {
  currentReport = data;
  const historyLink = $('history-detail-link');
  historyLink.classList.toggle('hidden', !data.history_id);
  if (data.history_id) historyLink.href = `/history/${encodeURIComponent(data.history_id)}`;
  progressPanel.classList.add('hidden');
  errorPanel.classList.add('hidden');
  $('score-value').textContent = String(data.score);
  $('score-meter-fill').style.width = `${Math.max(0, Math.min(100, data.relative_percentile))}%`;
  $('rank-description').textContent = `校准后从易到难第 ${data.target_rank} / ${data.total_problems} 位 · 梯度位置 ${data.relative_percentile}%`;
  const neighbor = data.calibration.neighbor_ratings;
  $('rating-neighbors').textContent = neighbor[0] == null ? `低于最低参考题 CF ${neighbor[1]}，按边界估计`
    : neighbor[1] == null ? `高于最高参考题 CF ${neighbor[0]}，按边界估计`
    : `相邻参考题 rating：${neighbor[0]} / ${neighbor[1]}`;
  const ratingRange = data.calibration.estimated_rating_interval;
  if (ratingRange && ratingRange[0] !== ratingRange[1]) {
    $('rating-neighbors').textContent += ` · 多个同样最优位置对应 CF ${ratingRange[0]}–${ratingRange[1]}`;
  }
  const stabilityLabels = { high: '高', medium: '中', low: '低' };
  $('stability-badge').textContent = `多轮排序稳定性：${stabilityLabels[data.calibration.stability] || '未知'}`;
  const clarity = data.statement_clarity;
  const clarityMax = clarity.max_score || 5;
  const clarityScore = Number(clarity.weighted_score ?? clarity.score);
  $('clarity-score').textContent = Number.isFinite(clarityScore) ? clarityScore.toFixed(1) : '—';
  $('clarity-max-label').textContent = `/ ${clarityMax}`;
  $('clarity-verdict-score').textContent = Number.isFinite(clarityScore)
    ? String(clarity.verdict_score ?? Math.floor(clarityScore + 0.5)) : '—';
  $('clarity-method').textContent = '总分保留一位小数 · 档位四舍五入';
  $('clarity-ring').style.setProperty('--clarity-angle', `${Math.max(0, Math.min(360, clarityScore / clarityMax * 360))}deg`);
  const verdictLabels = { 'strong reject': '强烈拒绝', reject: '拒绝', 'weak reject': '倾向拒绝',
    'weak accept': '倾向接受', accept: '接受', 'strong accept': '强烈接受' };
  $('clarity-status').textContent = clarity.verdict ? `${clarity.verdict} · ${verdictLabels[clarity.verdict]}`
    : clarity.passed ? '题意理解：及格' : '题意理解：未及格';
  $('clarity-status').classList.toggle('failed', !clarity.passed);
  $('clarity-summary').textContent = clarity.summary;
  $('neutral-statement').textContent = clarity.neutral_statement;
  const criteria = $('clarity-criteria');
  criteria.replaceChildren();
  for (const item of clarity.criteria) {
    const row = document.createElement('div'); row.className = 'clarity-criterion';
    const top = document.createElement('div'); top.className = 'clarity-criterion-top';
    const label = document.createElement('strong'); label.textContent = item.label;
    const weight = document.createElement('small'); weight.textContent = `权重 ${Math.round(item.weight * 100)}%`;
    const score = document.createElement('b'); score.textContent = `${item.score} / ${item.max_score || clarityMax}`;
    top.append(label, weight, score);
    const track = document.createElement('div'); track.className = 'clarity-criterion-track';
    const fill = document.createElement('span'); fill.style.width = `${item.score / (item.max_score || clarityMax) * 100}%`; track.append(fill);
    const verdict = document.createElement('span'); verdict.className = 'clarity-verdict';
    verdict.textContent = item.verdict ? `${item.verdict} · ${verdictLabels[item.verdict]}` : '';
    const note = document.createElement('p'); note.textContent = item.reason;
    row.append(top, track, verdict, note); criteria.append(row);
  }
  const issues = [...clarity.issues,
    ...clarity.unresolved_ambiguities.map((x) => `仅凭题面仍不明确：${x}`)];
  $('clarity-issues').classList.toggle('hidden', issues.length === 0);
  const issueList = $('clarity-issues-list'); issueList.replaceChildren();
  for (const issue of issues) {
    const li = document.createElement('li'); li.textContent = issue; issueList.append(li);
  }
  $('algorithm-value').textContent = data.analysis.algorithm;
  $('summary-value').textContent = data.analysis.summary;
  $('complexity-value').textContent = data.analysis.complexity;
  const tags = $('tag-list');
  tags.replaceChildren();
  for (const tag of data.analysis.inferred_tags) {
    const chip = document.createElement('span');
    chip.textContent = tag;
    tags.append(chip);
  }
  $('ranking-count').textContent = `${data.reference_count} 道参考题`;
  const list = $('ranking-list');
  list.replaceChildren();
  for (const item of data.ranking) {
    const row = document.createElement('li');
    if (item.target) row.classList.add('target');
    const number = document.createElement('span');
    number.className = 'rank-index'; number.textContent = String(item.rank).padStart(2, '0');
    const main = document.createElement('div'); main.className = 'rank-main';
    const title = document.createElement('span'); title.className = 'rank-title';
    const meta = document.createElement('span'); meta.className = 'rank-meta';
    const rating = document.createElement('span'); rating.className = 'rank-rating';
    if (item.target) {
      title.textContent = '你的题目'; meta.textContent = '校准后的难度位置'; rating.textContent = `≈ CF ${data.score}`;
    } else {
      title.append(makeLink(item.url, `${item.codeforces_id} · ${item.title}`));
      meta.append(makeLink(item.url, '查看题面 ↗'));
      if (item.editorial_url) meta.append(makeLink(item.editorial_url, '官方题解 ↗'));
      if (item.source_url) meta.append(makeLink(item.source_url, '解法代码 ↗'));
      if (item.source_method === 'editorial_template_composed') {
        const note = document.createElement('span'); note.textContent = '官方模板与题目代码组合'; meta.append(note);
      }
      rating.textContent = `CF ${item.rating}`;
    }
    main.append(title, meta);
    row.append(number, main, rating);
    list.append(row);
  }
  $('judge-reason-text').textContent = data.judge_reason || '模型未提供额外说明。';
  const c = data.calibration;
  const interval = c.equally_optimal_rank_interval;
  const intervalText = interval[0] === interval[1] ? `第 ${interval[0]} 位` : `第 ${interval[0]}–${interval[1]} 位`;
  $('calibration-note').textContent = `校准方法：${c.rounds} 轮匿名排序 → 按参考题 rating 做单调校准，并选择冲突最少的插入位置。原始目标顺位：${c.raw_target_ranks.join(' / ')}；同样最优的校准位置：${intervalText}；参考题顺序逆转率：${Math.round(c.anchor_inversion_rate * 100)}%。`;
  if (data.excluded_self_references?.length) {
    $('calibration-note').textContent += ` 已排除与上传题目重复的参考题：${data.excluded_self_references.join('、')}。`;
  }
  resultPanel.classList.remove('hidden');
  resultPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('download-button').addEventListener('click', () => {
  if (!currentReport) return;
  const blob = new Blob([JSON.stringify(currentReport, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = 'xcpc-problemjudger-report.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
