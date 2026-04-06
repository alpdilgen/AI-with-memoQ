import openai
import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

class AITranslator:
    def __init__(self, provider, api_key, model):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        
        if provider == "OpenAI":
            self.client = openai.OpenAI(api_key=api_key)
        elif provider == "Anthropic":
            self.client = anthropic.Anthropic(api_key=api_key)
            
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def translate_batch(self, prompt: str):
        """Sends prompt to AI with retries"""
        try:
            if self.provider == "OpenAI":
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a helpful translation assistant."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1
                )
                return response.choices[0].message.content, response.usage.total_tokens
                
            elif self.provider == "Anthropic":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4000,
                    temperature=0.1,
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                return response.content[0].text, response.usage.input_tokens + response.usage.output_tokens
                
        except Exception as e:
            print(f"AI API Error: {e}")
            raise e