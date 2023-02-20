from flask import Flask, request, render_template
from io import BytesIO
from PyPDF2 import PdfReader
import pandas as pd
from openai.embeddings_utils import get_embedding, cosine_similarity
import openai
import os
import base64
from flask_cors import CORS
from _md5 import md5
import redis
import json

app = Flask(__name__)

db = redis.StrictRedis(host='localhost', port=6379, db=0)

CORS(app)

class Chatbot():
    
    def extract_text(self, pdf):
        print("Parsing paper")
        number_of_pages = len(pdf.pages)
        print(f"Total number of pages: {number_of_pages}")
        paper_text = []
        for i in range(number_of_pages):
            page = pdf.pages[i]
            page_text = []

            def visitor_body(text, cm, tm, fontDict, fontSize):
                x = tm[4]
                y = tm[5]
                # ignore header/footer
                if (y > 50 and y < 720) and (len(text.strip()) > 1):
                    page_text.append({
                    'fontsize': fontSize,
                    'text': text.strip().replace('\x03', ''),
                    'x': x,
                    'y': y
                    })

            _ = page.extract_text(visitor_text=visitor_body)

            blob_font_size = None
            blob_text = ''
            processed_text = []

            for t in page_text:
                if t['fontsize'] == blob_font_size:
                    blob_text += f" {t['text']}"
                    if len(blob_text) >= 2000:
                        processed_text.append({
                            'fontsize': blob_font_size,
                            'text': blob_text,
                            'page': i
                        })
                        blob_font_size = None
                        blob_text = ''
                else:
                    if blob_font_size is not None and len(blob_text) >= 1:
                        processed_text.append({
                            'fontsize': blob_font_size,
                            'text': blob_text,
                            'page': i
                        })
                    blob_font_size = t['fontsize']
                    blob_text = t['text']
                paper_text += processed_text
        print("Done parsing paper")
        # print(paper_text)
        return paper_text

    def create_df(self, pdf):
        print('Creating dataframe')
        filtered_pdf= []
        # print(pdf.pages[0].extract_text())
        for row in pdf:
            if len(row['text']) < 30:
                continue
            filtered_pdf.append(row)
        df = pd.DataFrame(filtered_pdf)
        print(df.columns)
        # remove elements with identical df[text] and df[page] values
        df = df.drop_duplicates(subset=['text', 'page'], keep='first')
        # df['length'] = df['text'].apply(lambda x: len(x))
        print('Done creating dataframe')
        return df

    def embeddings(self, df):
        print('Calculating embeddings')
        # openai.api_key = os.getenv('OPENAI_API_KEY')
        embedding_model = "text-embedding-ada-002"
        embeddings = df.text.apply([lambda x: get_embedding(x, engine=embedding_model)])
        df["embeddings"] = embeddings
        print('Done calculating embeddings')
        return df

    def search(self, df, query, n=3, pprint=True):
        query_embedding = get_embedding(
            query,
            engine="text-embedding-ada-002"
        )
        df["similarity"] = df.embeddings.apply(lambda x: cosine_similarity(x, query_embedding))
        
        results = df.sort_values("similarity", ascending=False, ignore_index=True)
        # make a dictionary of the the first three results with the page number as the key and the text as the value. The page number is a column in the dataframe.
        results = results.head(n)
        sources = []
        for i in range(n):
            # append the page number and the text as a dict to the sources list
            sources.append({'Page '+str(results.iloc[i]['page']): results.iloc[i]['text'][:150]+'...'})
        print(sources)
        return {'results': results, 'sources': sources}
    
    def create_prompt(self, df, user_input):
        response = self.search(df, user_input, n=3)
        result = response['results']
        sources = response['sources']
        print(result)
        prompt = """You are a large language model whose expertise is reading and summarizing scientific papers. 
        You are given a query and a series of text embeddings from a paper in order of their cosine similarity to the query.
        You must take the given embeddings and return a very detailed summary of the paper that answers the query.
            
            Given the question: """+ user_input + """
            
            and the following embeddings as data: 
            
            1.""" + str(result.iloc[0]['text']) + """
            2.""" + str(result.iloc[1]['text']) + """
            3.""" + str(result.iloc[2]['text']) + """

            Return a detailed answer based on the paper:"""

        print('Done creating prompt')
        return {'prompt': prompt, 'sources': sources}

    def gpt(self, prompt, sources):
        print('Sending request to GPT-3')
        openai.api_key = os.getenv('OPENAI_API_KEY')
        r = openai.Completion.create(model="text-davinci-003", prompt=prompt, temperature=0.4, max_tokens=1500)
        answer = r.choices[0]['text']
        print('Done sending request to GPT-3')
        response = {'answer': answer, 'sources': sources}
        return response

@app.route("/", methods=["GET", "POST"])
def index():
    return render_template("index.html")

# a route to get the dataframe from the database
@app.route("/get_df", methods=['POST'])
def get_df():
    print('Getting dataframe')
    key = request.json['key']
    print(key)
    df = pd.DataFrame(json.loads(db.get(key)))
    print('Done getting dataframe')
    json_str = df.to_json(orient='records')
    print(len(json_str))
    print("Done processing pdf")
    return {"df": json_str}

# re-writing process_pdf to to just create the dataframe and send it to the frontend
@app.route("/process_pdf", methods=['POST'])
def process_pdf():
    print("Processing pdf")
    print(request)

    file = request.data

    key = md5(file).hexdigest()
    print(key)

    if db.get(key) is not None:
        print("File already exists")
        return {"key": key, "exists": True}

    pdf = PdfReader(BytesIO(file))
    chatbot = Chatbot()
    paper_text = chatbot.extract_text(pdf)
    df = chatbot.create_df(paper_text)

    json_str = df.to_json(orient='records')
    print(len(json_str))
    print("Done processing pdf")
    return {"key": key, "df": json_str}

# a function save that takes in a dataframe and saves it to gcs
@app.route("/save", methods=['POST'])
def save():
    print("Saving df to gcs")
    print(request.json)
    print(request.json['df'])
    print(request.json['key'])
    df = request.json['df']
    key = request.json['key']

    df = pd.DataFrame.from_dict(df)

    if df.empty:
        return {"error": "No data found"}

    if db.get(key) is None:
        db.set(key, df.to_json())
    else:
        print("File already exists")
        return {"key": key, "exists": True}
    
    print("Done processing pdf")
    return {"key": key, "exists": False}

@app.route("/download_pdf", methods=['POST'])
def download_pdf():

    print("Downloading pdf")
    # print(request.json)
    chatbot = Chatbot()
    url = request.json['url']
    data = request.json['data']
    data = base64.b64decode(data)

    print(url)
    # r = requests.get(str(url))    
    # print("Downloading pdf")
    # print(r.status_code)
    key = md5(data).hexdigest()

    if db.get(key) is not None:
        print("File already exists")
        return {"key": key, "exists": True}

    pdf = PdfReader(BytesIO(data))
    paper_text = chatbot.extract_text(pdf)
    df = chatbot.create_df(paper_text)

    json_str = df.to_json(orient='records')
    print(len(json_str))
    print("Done processing pdf")
    return {"key": key, "df": json_str}

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=True)