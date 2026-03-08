from openai import OpenAI
client = OpenAI(base_url="http://35.220.164.252:3888/v1", api_key="sk-osRZauVvCV9I2XqiLXzlFe4Til4BIDQKETG8u68RKRchkSDd")
print(client.responses.create(model="qwen3-max", input="ping"))
