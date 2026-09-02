import { useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { chat, generateQuestions, resetSession } from './api';

type Tab = 'chat' | 'generate';

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('chat');
  const [chatInput, setChatInput] = useState('');
  const [topic, setTopic] = useState('');
  const [count, setCount] = useState(3);
  const [difficulty, setDifficulty] = useState('小学高年级');
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const clearResponse = useCallback(() => {
    setResponse('');
    setError('');
  }, []);

  const handleReset = async () => {
    try {
      await resetSession();
      clearResponse();
      setChatInput('');
      setTopic('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleChat = async () => {
    if (!chatInput.trim()) return;
    clearResponse();
    setLoading(true);

    let full = '';
    await chat(
      chatInput.trim(),
      (chunk) => {
        full += chunk;
        setResponse(full);
      },
      () => {
        setLoading(false);
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      },
    );
  };

  const handleGenerate = async () => {
    if (!topic.trim()) return;
    clearResponse();
    setLoading(true);

    let full = '';
    await generateQuestions(
      topic.trim(),
      count,
      difficulty,
      (chunk) => {
        full += chunk;
        setResponse(full);
      },
      () => {
        setLoading(false);
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      },
    );
  };

  return (
    <div className="container">
      <header>
        <h1>数学导师</h1>
        <p>基于 nova_ai + nova_agent 的智能教学助手</p>
      </header>

      <div className="status-bar">
        <span>模式：{activeTab === 'chat' ? '简单问答' : '一键出题'}</span>
        <button onClick={handleReset} disabled={loading}>
          清空对话
        </button>
      </div>

      <div className="card">
        <div className="tabs">
          <button
            className={`tab ${activeTab === 'chat' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('chat');
              clearResponse();
            }}
          >
            简单问答
          </button>
          <button
            className={`tab ${activeTab === 'generate' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('generate');
              clearResponse();
            }}
          >
            一键出题
          </button>
        </div>

        {activeTab === 'chat' ? (
          <>
            <textarea
              placeholder="输入你的数学问题，例如：如何理解一元二次方程？"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              disabled={loading}
            />
            <div className="actions">
              <button onClick={handleChat} disabled={loading || !chatInput.trim()}>
                {loading ? (
                  <span className="loading">
                    <span className="spinner" />
                    思考中...
                  </span>
                ) : (
                  '发送问题'
                )}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="form-row">
              <input
                type="text"
                placeholder="知识点，例如：分数加减法"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                disabled={loading}
              />
              <select
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value)}
                disabled={loading}
              >
                <option value="小学低年级">小学低年级</option>
                <option value="小学高年级">小学高年级</option>
                <option value="初中">初中</option>
                <option value="高中">高中</option>
              </select>
              <input
                type="number"
                min={1}
                max={10}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                disabled={loading}
              />
            </div>
            <div className="actions">
              <button onClick={handleGenerate} disabled={loading || !topic.trim()}>
                {loading ? (
                  <span className="loading">
                    <span className="spinner" />
                    生成中...
                  </span>
                ) : (
                  '一键出题'
                )}
              </button>
            </div>
          </>
        )}
      </div>

      <div className="card">
        <h2>结果</h2>
        {error ? (
          <div className="response" style={{ color: '#dc2626' }}>
            错误：{error}
          </div>
        ) : (
          <div className="response markdown-body">
            {response ? <ReactMarkdown>{response}</ReactMarkdown> : null}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
