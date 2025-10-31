import os
import json
import sqlite3
import uuid
import base64
from flask import Flask, render_template, request, jsonify, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import logging

# --- Imports do ReportLab ---
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader

# --- Configuração Inicial ---
# ... (código existente, sem alterações) ...
load_dotenv()
app = Flask(__name__)

# Configura o logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuração do Cliente OpenAI (Oficial) ---
# ... (código existente, sem alterações) ...
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    # base_url= os.getenv("base_url") # Comentado como solicitado
)
MODELO_IA = "gpt-4o-mini"

# --- Configuração do Banco de Dados SQLite3 ---
# ... (código existente, sem alterações) ...
DB_NAME = 'os_files.db'
# Define PDF_DIR no diretório 'static' que o Flask pode servir
PDF_DIR = os.path.join(app.root_path, 'static', 'pdf')
os.makedirs(PDF_DIR, exist_ok=True)

def init_db():
# ... (código existente, sem alterações) ...
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS generated_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()

def add_file_to_db(filename):
# ... (código existente, sem alterações) ...
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO generated_files (filename) VALUES (?)", (filename,))
    conn.commit()
    conn.close()
    logger.info(f"Arquivo {filename} adicionado ao DB.")

def get_files_to_delete():
# ... (código existente, sem alterações) ...
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Pega arquivos com mais de 5 minutos
    cursor.execute("SELECT filename FROM generated_files WHERE created_at <= datetime('now', '-5 minutes')")
    files = [row[0] for row in cursor.fetchall()]
    conn.close()
    return files

def delete_file_record(filename):
# ... (código existente, sem alterações) ...
    # Deleta o arquivo físico
    try:
        file_path = os.path.join(PDF_DIR, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Arquivo físico deletado: {file_path}")
        else:
            logger.warning(f"Arquivo físico não encontrado para deleção: {file_path}")
    except Exception as e:
        logger.error(f"Erro ao deletar arquivo físico {filename}: {e}")
        
    # Deleta o registro do DB
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM generated_files WHERE filename = ?", (filename,))
        conn.commit()
        conn.close()
        logger.info(f"Registro do DB deletado: {filename}")
    except Exception as e:
        logger.error(f"Erro ao deletar registro do DB {filename}: {e}")

# --- Tarefa de Limpeza Agendada ---
def cleanup_old_files():
# ... (código existente, sem alterações) ...
    logger.info("Executando tarefa de limpeza...")
    files_to_delete = get_files_to_delete()
    if not files_to_delete:
        logger.info("Nenhum arquivo antigo para limpar.")
        return
        
    for filename in files_to_delete:
        logger.info(f"Limpando arquivo antigo: {filename}")
        delete_file_record(filename)

# --- O Cérebro do Chatbot (System Prompt V3.1 - Com Correção de Fluxo) ---
SYSTEM_PROMPT = """
Você é um assistente de terminal focado em criar Ordens de Serviço (OS) para uma oficina.
Seu objetivo é coletar as informações do usuário de forma conversacional, seguindo um roteiro fixo, para preencher uma estrutura de dados JSON.

REGRAS PRINCIPAIS:
1.  **UMA PERGUNTA DE CADA VEZ**: Siga o roteiro abaixo e faça UMA ÚNICA pergunta por vez.
2.  **PULAR ETAPAS**: O usuário pode digitar 'p' ou 'pular' para pular QUALQUER pergunta. Se ele pular, preencha o campo com "" (string vazia) e vá para a próxima pergunta.
3.  **SEJA DIRETO**: Não adicione comentários, apenas faça a pergunta do roteiro. Use emojis 🔧🏁📝 para um tom amigável.
4.  **UPLOAD DE LOGO**: Se o usuário enviar `[LOGO_ANEXADO]`, coloque `"[LOGO_PLACEHOLDER]"` no campo `logo_data_base64` e vá para a próxima pergunta.
5.  **FLUXO DE CORREÇÃO**: Após coletar tudo (Blocos 1-5), você DEVE ir para o Bloco 6 (Resumo). Se o usuário pedir para corrigir (ex: 'cliente'), você DEVE recomeçar as perguntas daquele bloco (ex: Bloco 1). Após o bloco corrigido terminar, você DEVE voltar para o Bloco 6 (Resumo) novamente.
6.  **FORMATO FINAL**: Somente quando o usuário digitar 'sim' ou 's' no Bloco 7, sua última mensagem DEVE ser a tag [GERAR_PDF] seguida do JSON completo.

--- ROTEIRO (Siga Exatamente) ---

**Bloco 1: Início e Cliente**
1.  Saudação: "Olá! 🏁 Vamos iniciar uma nova Ordem de Serviço. Para pular qualquer etapa, digite `p` ou `pular`."
2.  Pergunta: "Qual o nome do cliente? 📝"
3.  Pergunta: "Qual o telefone dele? (ou 'p' para pular)"
4.  Pergunta: "Qual o endereço? (ou 'p' para pular)"
4.  Pergunta: "Qual o CPF/CNPJ do cliente? (ou 'p' para pular)"
    (FIM DO BLOCO 1. Próximo passo: Se você veio do Bloco 7 (Correção), volte IMEDIATAMENTE para o Bloco 6 (Resumo). Senão, vá para o Bloco 2.)

**Bloco 2: Veículo**
1.  Pergunta: "Certo. Agora os dados do veículo. 🔧 Qual a placa? (ou 'p' para pular)"
2.  Pergunta: "Qual a marca e modelo? (Ex: Fiat Palio) (ou 'p' para pular)"
3.  Pergunta: "E qual o ano do veículo? (ou 'p' para pular)"
    (FIM DO BLOCO 2. Próximo passo: Se você veio do Bloco 7 (Correção), volte IMEDIATAMENTE para o Bloco 6 (Resumo). Senão, vá para o Bloco 3.)

**Bloco 3: Serviços (Loop)**
1.  Pergunta: "Perfeito. Qual seria o serviço / peça trocada no veículo e seu preço? (Ex: Pintura capô, 500, Leo) (ou 'p' para não adicionar serviços)"
    (Se 'p', pule para o Bloco 4)
2.  (IA processa. Se faltar 'descricao' ou 'valor', pergunte: "Qual a descrição?" ou "Qual o valor?")
3.  Pergunta: "Qual o responsável pelo serviço? (ou 'p' para pular)"
4.  Pergunta de Loop: "Serviço adicionado. Gostaria de adicionar mais algum serviço / produto na OS? (s/n)"
    (Se 's', pergunte: "Ok. Qual o próximo serviço / produto na OS?" e repita o Bloco 3)
    (Se 'n', FIM DO BLOCO 3. Próximo passo: Se você veio do Bloco 7 (Correção), volte IMEDIATAMENTE para o Bloco 6 (Resumo). Senão, vá para o Bloco 4.)

**Bloco 4: Observações**
1.  Pergunta: "Gostaria de adicionar alguma observação? (s/n)"
    (Se 'n' ou 'p', FIM DO BLOCO 4. Próximo passo: Se você veio do Bloco 7 (Correção), volte IMEDIATAMENTE para o Bloco 6 (Resumo). Senão, vá para o Bloco 5.)
2.  Pergunta: "Qual a observação? (ou 'p' para pular)"
    (FIM DO BLOCO 4. Próximo passo: Se você veio do Bloco 7 (Correção), volte IMEDIATAMENTE para o Bloco 6 (Resumo). Senão, vá para o Bloco 5.)

**Bloco 5: Dados da Oficina**
1.  Pergunta: "Estamos finalizando. Qual o nome da sua oficina? 🔧 (ou 'p' para pular)"
2.  Pergunta: "Qual o CNPJ da oficina? (ou 'p' para pular)"
3.  Pergunta: "Qual o endereço da sua oficina? (Ex: Rua X, 10 - Bairro, Cidade - RJ) (ou 'p' para pular)"
4.  Pergunta: "Qual o telefone da sua oficina? (ou 'p' para pular)"
5.  Pergunta: "Você tem um arquivo de logo para carregar? O upload aparecerá no chat. (ou 'p' para pular)"
    (FIM DO BLOCO 5. Próximo passo: Volte IMEDIATAMENTE para o Bloco 6 (Resumo).)

**Bloco 6: Resumo (IMPORTANTE)**
1.  Mensagem: "OK, dados coletados. Aqui está um resumo para sua revisão: 📝"
2.  Mensagem (Exemplo de formato, use os dados reais coletados):
    **Resumo da OS:**
    **Oficina:**
    - Nome: (Nome da Oficina)
    - CNPJ: (CNPJ)
    - Logo: (Sim, se [LOGO_PLACEHOLDER], ou Não/Pulado)
    **Cliente:**
    - Nome: (Nome do Cliente)
    - Telefone: (Telefone)
    **Veículo:**
    - Placa: (Placa)
    - Modelo: (Marca/Modelo)
    **Serviços/Venda:**
    1. (Descrição), (Responsável), R$ (Valor)
    2. (Descrição), (Responsável), R$ (Valor)
    **Observações:**
    - (Observações)
3.  (Após enviar o resumo, IMEDIATAMENTE vá para o Bloco 7)

**Bloco 7: Correção (Loop de Edição)**
1.  Pergunta: "Os dados estão corretos? Digite 'sim' (ou 's') para gerar o PDF, ou o que deseja corrigir (ex: 'oficina', 'cliente', 'veiculo', 'servicos', 'obs'). 🔧"
    (Analise a resposta do usuário)
    - Se 'sim' ou 's' -> Vá para o Bloco 8 (Finalização).
    - Se 'cliente' -> Responda "Ok, vamos corrigir o cliente." e vá para a Pergunta 2 do Bloco 1.
    - Se 'veiculo' -> Responda "Ok, vamos corrigir o veículo." e vá para a Pergunta 1 do Bloco 2.
    - Se 'servicos' -> Responda "Ok, vamos corrigir os serviços." e vá para a Pergunta 1 do Bloco 3.
    - Se 'obs' -> Responda "Ok, vamos corrigir as observações." e vá para a Pergunta 1 do Bloco 4.
    - Se 'oficina' -> Responda "Ok, vamos corrigir os dados da oficina." e vá para a Pergunta 1 do Bloco 5.
    (Após o bloco corrigido terminar, você DEVE retornar ao Bloco 6 - Resumo)

**Bloco 8: Finalização**
1.  (Acionado por 'sim'/'s' no Bloco 7)
2.  Resposta: [GERAR_PDF] { ...JSON completo... }

--- ESTRUTURA JSON FINAL ---
[GERAR_PDF]
{
  "oficina": {
    "nome": "...",
    "cnpj": "...",
    "endereco": "...",
    "cidade_estado": "...",
    "telefone": "...",
    "logo_data_base64": "..." // "[LOGO_PLACEHOLDER]" ou ""
  },
  "cliente": {
    "nome": "...",
    "telefone": "...",
    "documento": "...",
    "endereco": "..."
  },
  "veiculo": {
    "marca": "...",
    "modelo": "...",
    "ano": "...",
    "placa": "..."
  },
  "servicos": [
    {"descricao": "...", "responsavel": "...", "valor": 0.00}
  ],
  "observacoes": "..."
}
"""

# --- Funções do ReportLab (Modificadas) ---

def header_callback_sem_rodape(canvas, doc, logo_path, oficina_info):
# ... (código existente, sem alterações, exceto o try/except já corrigido) ...
    canvas.saveState()
    styles = getSampleStyleSheet()
    
    # 1. Logo
    logo_drawn = False
    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image(logo_path) 
            logo.drawHeight = 0.7 * 72
            logo.drawWidth = 0.7 * 72
            logo.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - logo.drawHeight - 10)
            logo_drawn = True
        except Exception as e:
            logger.warning(f"Não foi possível carregar a imagem do logo de {logo_path}: {e}")
    elif logo_path:
         logger.warning(f"Caminho do logo '{logo_path}' foi passado para o callback, mas não foi encontrado.")
    
    # 2. Informações da Oficina
# ... (código existente, sem alterações) ...
    style_header_title = ParagraphStyle(name='HeaderTitle', parent=styles['Heading1'], fontSize=16, alignment=TA_CENTER, spaceAfter=4)
    style_header_address = ParagraphStyle(name='HeaderAddress', parent=styles['Normal'], fontSize=9, alignment=TA_CENTER, leading=10)
    
    nome_oficina = oficina_info.get('nome', 'NOME DA OFICINA')
    cnpj_oficina = oficina_info.get('cnpj', 'CNPJ NÃO INFORMADO')
    endereco_oficina = oficina_info.get('endereco', 'Endereço não informado')
    cidade_estado = oficina_info.get('cidade_estado', endereco_oficina) 
    telefone_oficina = oficina_info.get('telefone', 'Telefone não informado')
    
    p_title = Paragraph(f"<b>{nome_oficina}</b>", style_header_title)
    p_address = Paragraph(f"{cidade_estado}<br/>CNPJ: {cnpj_oficina} | Tel: {telefone_oficina}", style_header_address)
    
    titulo_width = doc.width - 2 * doc.leftMargin
    p_title.wrapOn(canvas, titulo_width, 50)
    p_title.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - 30)
    
    p_address.wrapOn(canvas, titulo_width, 50)
    p_address.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - 60)
    
    canvas.restoreState()

# Função principal de geração de PDF
def gerar_os_pintura_carro_profissional(dados_os, nome_arquivo_completo):
    
# ... (código existente, sem alterações) ...
    doc = SimpleDocTemplate(nome_arquivo_completo, pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=100, bottomMargin=40)
    
    styles = getSampleStyleSheet()
    
    # Estilos (como no seu original)
    styles.add(ParagraphStyle(name='TitleOS', parent=styles['h1'], fontSize=18, alignment=TA_CENTER, spaceAfter=10, textColor=colors.HexColor('#003366')))
    styles.add(ParagraphStyle(name='SectionHeading', parent=styles['h2'], fontSize=12, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(name='FieldValue', parent=styles['Normal'], fontSize=10, spaceAfter=3))
    styles.add(ParagraphStyle(name='FieldLabel', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#666666')))
    styles.add(ParagraphStyle(name='TableHeading', parent=styles['Normal'], fontSize=9, alignment=TA_CENTER, textColor=colors.whitesmoke))
    styles.add(ParagraphStyle(name='TableData', parent=styles['Normal'], fontSize=9, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='TableTotalLabel', parent=styles['h3'], fontSize=11, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='TableTotalValue', parent=styles['h3'], fontSize=11, alignment=TA_CENTER, textColor=colors.red))

    story = []

    # *** MUDANÇA DE TEXTO ***
    story.append(Paragraph("<b>ORDEM DE SERVIÇO / VENDA</b>", styles['TitleOS']))

    # Info OS
# ... (código existente, sem alterações) ...
    os_info_data = [
        [Paragraph(f"<b>Nº OS:</b> {dados_os['numero_os']}", styles['FieldLabel']), Paragraph(f"<b>Data:</b> {dados_os['data_os']}", styles['FieldLabel'])]
    ]
    story.append(Table(os_info_data, colWidths=[doc.width/2.0]*2, style=TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('BOTTOMPADDING', (0,0), (-1,-1), 4)])))
    story.append(Spacer(1, 8))

    # Dados do Cliente
# ... (código existente, sem alterações) ...
    story.append(Paragraph("<b>DADOS DO CLIENTE</b>", styles['SectionHeading']))
    cliente_data = [
        [Paragraph(f"<b>Nome:</b> {dados_os['cliente'].get('nome', '-')}", styles['FieldValue']),
         Paragraph(f"<b>Telefone:</b> {dados_os['cliente'].get('telefone', '-')}", styles['FieldValue'])],
        [Paragraph(f"<b>CPF/CNPJ:</b> {dados_os['cliente'].get('documento', '-')}", styles['FieldValue']),
         Paragraph(f"<b>Endereço:</b> {dados_os['cliente'].get('endereco', '-')}", styles['FieldValue'])]
    ]
    story.append(Table(cliente_data, colWidths=[doc.width/2.0]*2, style=TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('BOTTOMPADDING', (0,0), (-1,-1), 4)])))
    story.append(Spacer(1, 8))

    # Dados do Veículo
# ... (código existente, sem alterações) ...
    story.append(Paragraph("<b>DADOS DO VEÍCULO</b>", styles['SectionHeading']))
    veiculo_data = [
        [Paragraph(f"<b>Marca:</b> {dados_os['veiculo'].get('marca', '-')}", styles['FieldValue']),
         Paragraph(f"<b>Modelo:</b> {dados_os['veiculo'].get('modelo', '-')}", styles['FieldValue'])],
        [Paragraph(f"<b>Ano:</b> {dados_os['veiculo'].get('ano', '-')}", styles['FieldValue']),
         Paragraph(f"<b>Placa:</b> {dados_os['veiculo'].get('placa', '-')}", styles['FieldValue'])]
    ]
    story.append(Table(veiculo_data, colWidths=[doc.width/2.0]*2, style=TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('BOTTOMPADDING', (0,0), (-1,-1), 4)])))
    story.append(Spacer(1, 8))

    # *** MUDANÇA DE TEXTO ***
    story.append(Paragraph("<b>DETALHES DO SERVIÇO / VENDA</b>", styles['SectionHeading']))
    servico_table_headers = [
# ... (código existente, sem alterações) ...
        Paragraph("<b>ITEM</b>", styles['TableHeading']),
        Paragraph("<b>DESCRIÇÃO</b>", styles['TableHeading']),
        Paragraph("<b>RESPONSÁVEL</b>", styles['TableHeading']),
        Paragraph("<b>VALOR (R$)</b>", styles['TableHeading'])
    ]
    servico_rows = [servico_table_headers]
# ... (código existente, sem alterações) ...
    total_servicos = 0.0
    
    servicos = dados_os.get('servicos', [])
# ... (código existente, sem alterações) ...
    if servicos:
        for i, item in enumerate(servicos):
            valor = 0.0
# ... (código existente, sem alterações) ...
            try:
                valor = float(item.get('valor', 0.0))
            except (ValueError, TypeError):
                valor = 0.0
                
            servico_rows.append([
# ... (código existente, sem alterações) ...
                Paragraph(str(i+1), styles['TableData']),
                Paragraph(item.get('descricao', '-'), styles['TableData']),
                Paragraph(item.get('responsavel', '-'), styles['TableData']),
                Paragraph(f"{valor:.2f}", styles['TableData'])
            ])
            total_servicos += valor
    
    tabela_servicos = Table(servico_rows,
# ... (código existente, sem alterações) ...
                            colWidths=[0.5*72, 3.0*72, 2.0*72, 1.5*72],
                            repeatRows=1)
    
    tabela_servicos.setStyle(TableStyle([
# ... (código existente, sem alterações) ...
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#DDDDDD')),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(tabela_servicos)
    story.append(Spacer(1, 12))

    # Total Geral
# ... (código existente, sem alterações) ...
    total_data = [
        [Paragraph("", styles['Normal']),
         Paragraph("<b>TOTAL GERAL (R$)</b>", styles['TableTotalLabel']),
         Paragraph(f"<b>{total_servicos:.2f}</b>", styles['TableTotalValue'])]
    ]
    tabela_total = Table(total_data, colWidths=[4.0*72, 2.0*72, 1.5*72], style=TableStyle([
        ('ALIGN', (1, 0), (2, 0), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (1, 0), (2, 0), 1, colors.HexColor('#003366')),
        ('BACKGROUND', (1, 0), (2, 0), colors.HexColor('#E0F2F7')),
    ]))
    story.append(tabela_total)
    story.append(Spacer(1, 24))

    # Observações
# ... (código existente, sem alterações) ...
    story.append(Paragraph("<b>OBSERVAÇÕES</b>", styles['SectionHeading']))
    story.append(Paragraph(dados_os.get('observacoes', '-'), styles['FieldValue']))
    story.append(Spacer(1, 30))

    # Assinaturas
    story.append(Paragraph("<b>ASSINATURAS</b>", styles['SectionHeading']))
    assinaturas_data = [
        ["", ""],
        [Paragraph("_____________________________", styles['Normal']), Paragraph("_____________________________", styles['Normal'])],
        # *** MUDANÇA DE TEXTO ***
        [Paragraph("<b>Assinatura do Cliente</b>", styles['Normal']), Paragraph("<b>Assinatura do Responsável</b>", styles['Normal'])]
    ]
    story.append(Table(assinaturas_data, colWidths=[doc.width/2.0]*2, style=TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
        ('TOPPADDING', (0,0), (-1,-1), 15),
    ])))

    # --- Build ---
    
# ... (código existente, sem alterações) ...
    oficina_info = dados_os.get('oficina', {})
    logo_data_base64 = oficina_info.get('logo_data_base64', '')
    
    logo_to_use = None
    temp_logo_to_delete = None
    
    if logo_data_base64 and logo_data_base64.startswith('data:image/'):
# ... (código existente, sem alterações) ...
        try:
            header, img_data_b64 = logo_data_base64.split(',', 1)
            img_type = header.split(';')[0].split('/')[1]
            img_data = base64.b64decode(img_data_b64)
            
            temp_filename = f"{uuid.uuid4().hex}_logo.{img_type}"
            temp_filepath = os.path.join(PDF_DIR, temp_filename)
            
            with open(temp_filepath, 'wb') as f:
                f.write(img_data)
            
            logo_to_use = temp_filepath
            temp_logo_to_delete = temp_filename
            logger.info(f"Logo Base64 decodificado e salvo em: {temp_filepath}")
            
        except Exception as e:
            logger.error(f"Erro ao decodificar e salvar logo Base64: {e}")
            logo_to_use = None
    
    callback_func = lambda c, d: header_callback_sem_rodape(c, d, logo_to_use, oficina_info)
    
    doc.build(story,
              onFirstPage=callback_func,
              onLaterPages=callback_func)
    
    logger.info(f"PDF gerado com sucesso: {nome_arquivo_completo}")
    
    return temp_logo_to_delete


# --- Rotas Flask ---

@app.route('/')
def index():
# ... (código existente, sem alterações) ...
    return render_template('index.html')

@app.route('/download/<filename>')
def download_file(filename):
# ... (código existente, sem alterações) ...
    return send_from_directory(PDF_DIR, filename, as_attachment=True)

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        history = data.get('history', [])
        user_message = data.get('message')
        
        logo_data_from_client = data.get('logo_data') 

        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}] + history
        if user_message:
            messages.append({'role': 'user', 'content': user_message})
            
        if user_message == '[LOGO_ANEXADO]':
             logger.info("Recebida mensagem de placeholder [LOGO_ANEXADO]. Enviando para IA.")
        
        response = client.chat.completions.create(
            model=MODELO_IA,
            messages=messages,
            max_tokens=4096, 
            temperature=0.2
        )
        
        ai_response_content = response.choices[0].message.content

        # --- Verificação da Geração do PDF ---
        if "[GERAR_PDF]" in ai_response_content:
            logger.info("Tag [GERAR_PDF] detectada. Iniciando geração do PDF.")
            
            json_data_str = ai_response_content.split("[GERAR_PDF]", 1)[1].strip()
            dados_coletados = json.loads(json_data_str)

            if logo_data_from_client and dados_coletados.get('oficina', {}).get('logo_data_base64') == '[LOGO_PLACEHOLDER]':
                logger.info("Substituindo placeholder do logo pelos dados Base64 recebidos.")
                dados_coletados['oficina']['logo_data_base64'] = logo_data_from_client
            elif dados_coletados.get('oficina', {}).get('logo_data_base64') == '[LOGO_PLACEHOLDER]':
                 logger.warning("IA retornou placeholder de logo, mas nenhum dado de logo foi recebido do cliente.")
                 dados_coletados['oficina']['logo_data_base64'] = ""
            
            numero_os_curto = f"OS{datetime.now().strftime('%y%m%d-%H%M')}"
            dados_finais_os = {
                "numero_os": numero_os_curto,
                "data_os": datetime.now().strftime("%d/%m/%Y"),
                
                "oficina": dados_coletados.get("oficina", {}),
                "cliente": dados_coletados.get("cliente", {}),
                "veiculo": dados_coletados.get("veiculo", {}),
                "servicos": dados_coletados.get("servicos", []),
                "observacoes": dados_coletados.get("observacoes", "")
            }
            
            placa = dados_finais_os["veiculo"].get("placa", "SEM_PLACA").replace("-","")
            unique_id = str(uuid.uuid4())[:4]
            filename = f"{numero_os_curto}_{placa}_{unique_id}.pdf"
            full_path = os.path.join(PDF_DIR, filename)
            
            temp_logo_filename = gerar_os_pintura_carro_profissional(dados_finais_os, full_path)
            
            add_file_to_db(filename)
            if temp_logo_filename:
                add_file_to_db(temp_logo_filename)
            
            return jsonify({
                'type': 'pdf',
                'message': 'Ordem de Serviço gerada! Clique abaixo para baixar.',
                'url': f'/download/{filename}'
            })

        else:
            # Retorno de chat normal
            return jsonify({
                'type': 'chat',
                'message': ai_response_content
            })

    except Exception as e:
        logger.error(f"Erro na rota /chat: {e}")
        if 'context_length_exceeded' in str(e):
             return jsonify({'type': 'error', 'message': 'Erro: O histórico da conversa é muito longo.'}), 400
        return jsonify({'type': 'error', 'message': f'Ocorreu um erro no servidor: {e}'}), 500


# --- Inicialização ---
if __name__ == '__main__':
# ... (código existente, sem alterações) ...
    init_db()
    
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(cleanup_old_files, 'interval', minutes=1)
    scheduler.start()
    
    logger.info("Iniciando o servidor Flask...")
    app.run(debug=True, host='0.0.0.0', port=5000)


