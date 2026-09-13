// Virtual users post the golden set's questions in turn, with no pause between requests,
// each keeping its connection as a real client would. load/run.py runs this as a Job in the
// cluster, against the API's Service; see load/k6-job.yaml.
import http from 'k6/http';
import exec from 'k6/execution';
import { Counter } from 'k6/metrics';

const input = JSON.parse(open('/input/input.json'));
const url = `${__ENV.TARGET}/questions`;
const params = {
  headers: { 'content-type': 'application/json', 'x-api-key': __ENV.API_KEY },
  tags: { name: 'POST /questions' },
};

// The classes the criteria count, which k6's own metrics do not separate: a 5xx, any status
// but 201, and a request that got no response at all. k6 leaves a counter nothing was added
// to out of its summary, so a missing one means zero.
const serverErrors = new Counter('responses_5xx');
const notCreated = new Counter('responses_not_201');
const noResponse = new Counter('requests_without_response');

export const options = {
  scenarios: {
    questions: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS),
      duration: __ENV.DURATION,
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

export default function () {
  const question = input.questions[exec.scenario.iterationInTest % input.questions.length];
  const body = JSON.stringify({ collection_id: input.collection_id, question });
  const response = http.post(url, body, params);
  if (response.status === 0) noResponse.add(1);
  if (response.status >= 500) serverErrors.add(1);
  if (response.status !== 201) notCreated.add(1);
}

// Between markers, so load/run.py can find the summary among k6's own log lines.
export function handleSummary(data) {
  return { stdout: `===K6-SUMMARY===\n${JSON.stringify(data)}\n===END-K6-SUMMARY===\n` };
}
