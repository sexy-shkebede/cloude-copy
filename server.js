const express = require('express');
const cors = require('cors');
const fetch = require('node-fetch');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname)));

const API_URL = 'http://195.208.3.238:4500/v1/chat/completions';
const API_KEY = 'sk_live_a48c11df517abcc9';
const MODEL_NAME = 'claude-fable-5';

let chatHistory = [];

app.post('/api/chat', async (req, res) => {
    try {
        const { message } = req.body;
        
        chatHistory.push({ role: 'user', content: message });

        const response = await fetch(API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${API_KEY}`
            },
            body: JSON.stringify({
                model: MODEL_NAME,
                messages: chatHistory,
                stream: false
            })
        });

        const data = await response.json();

        if (!response.ok) {
            return res.status(response.status).json(data);
        }

        const aiReply = data.choices[0].message.content;
        chatHistory.push({ role: 'assistant', content: aiReply });

        res.json({ reply: aiReply });
    } catch (error) {
        console.error('API Error:', error);
        res.status(500).json({ error: 'Internal Server Error', details: error.message });
    }
});

app.post('/api/reset', (req, res) => {
    chatHistory = [];
    res.json({ success: true });
});

app.listen(PORT, () => {
    console.log(`Server started at http://localhost:${PORT}`);
});
