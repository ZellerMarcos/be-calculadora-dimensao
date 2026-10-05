# be-calculadora-dimensao

## API local

Instale as dependências listadas em `requirement.txt` e inicie o servidor na raiz deste repositório:

```powershell
python -m pip install -r requirement.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

O frontend Vite encaminha `/api` para `http://127.0.0.1:8000` durante o desenvolvimento. A Tabela 5 versionada fica em `app/data/tabela5.json`; os repositórios trocam os dados por HTTP, sem arquivo compartilhado externo. A documentação OpenAPI fica em `/docs`.

O arquivo `.env` local define `CORS_ORIGINS`; copie `.env.example` como referência. No Render, configure `CORS_ORIGINS` nas variáveis de ambiente do serviço backend com a origem pública do frontend (por exemplo, `https://seu-frontend.onrender.com`, sem barra final). Não é necessário publicar o arquivo `.env`.

Python 3.11 ou superior é necessário. Os limites defensivos das entradas são: projeção horizontal e extensão até 500 m, altura até 200 m, intensidade até 1.000 mm/h, largura/diâmetro e lâmina até 5.000 mm, bordo livre até 1.000 mm, declividade até 100%, passo construtivo entre 1 e 500 mm e projeto até 120 caracteres por campo. Rugosidade aceita somente os quatro valores da Tabela 2. Esses limites validam entradas, não substituem os critérios do responsável técnico.

## Endpoints

Todos usam o prefixo `/api/v1/nbr10844`.

`GET /health` confirma que o servidor iniciou. Resposta: `{"status":"ok","norma":"ABNT NBR 10844:1989","versaoMotor":"0.2.0"}`.

`GET /materiais` retorna IDs, valores `n` e descrições da Tabela 2. Exemplo: `{"id":"plastico_fibrocimento_aco_naoferrosos","n":0.011,"nome":"Plástico, fibrocimento, aço e metais não-ferrosos"}`.

`GET /localidades?q=Rio&uf=RJ` filtra os postos por nome e UF. `GET /localidades/5/intensidade?periodoRetorno=5` retorna o posto, período solicitado, intensidade, período real quando houver e alerta de divergência.

`GET /tabela5` retorna o JSON completo versionado, incluindo os 98 postos, valores indisponíveis como `null` e os períodos reais entre parênteses no campo `periodosReais`.

`POST /calhas/verificar` e `POST /calhas/dimensionar` recebem as entradas em JSON. Exemplo reduzido:

```json
{
	"station_id": 5,
	"return_period": 5,
	"rainfall_source": "station",
	"roof_width": 10,
	"roof_rise": 2.5,
	"roof_surface": "inclined",
	"gutter_length": 12,
	"outlet_type": "extremidade",
	"profile": "rectangular",
	"bottom_width_mm": 180,
	"top_width_mm": 180,
	"useful_depth_mm": 100,
	"freeboard_mm": 30,
	"constructive_step_mm": 10,
	"slope_percent": 1,
	"roughness": 0.011,
	"curve_condition": "none",
	"gutter_type": "beiral_platibanda"
}
```

A resposta inclui `status`, `resultados`, `alertas`, `passos` e `metadados`. O dimensionamento acrescenta `dimensionamento` com a dimensão mínima ou motivo de não atendimento. Para saída central, informe `outlet_type: "central"`, `extension_side_a` e `extension_side_b`; para intensidade manual informe `rainfall_source: "manual"`, `intensity` e `manual_justification`.

`POST /memorial?formato=json|docx&tipo=verificar|dimensionar` aceita o mesmo corpo, recalcula a operação solicitada e gera o memorial completo. DOCX é enviado como arquivo do Word; JSON inclui entradas, premissas, fórmulas, substituições, resultados, alertas e conclusão.

O corpo contém as entradas do projeto; nenhum resultado calculado pelo navegador é aceito. Os campos de projeto são limitados e inseridos como texto no DOCX, sem persistência.