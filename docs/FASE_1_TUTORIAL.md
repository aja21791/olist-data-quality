# Fase 1 — Tutorial detallado de preparación (Windows 11)

**Curso aplicado:** Calidad de datos con SQL, DuckDB y Python. Este material continúa la práctica de *Data Preparation* y *Data Wrangling* (exploración, limpieza, transformación y validación) y sigue la arquitectura fijada en el enunciado.

> **En esta fase no se limpian datos ni se fabrican métricas.** Se prepara un entorno repetible, la base persistente, los CSV originales y un inventario verificable de las fuentes. Al acabar deben funcionar los comandos descritos, con los datos de Olist descargados.

La carpeta del proyecto se denomina `olist-data-quality`; todas las instrucciones de línea de comandos, salvo indicación distinta, se ejecutan **en Miniconda Prompt, dentro de esa carpeta**. Los bloques de código que aparecen en las secciones correspondientes también están implementados en archivos del paquete descargable; no debes copiarlos de nuevo si utilizas la plantilla.

---

## Paso 1. Entender el problema, el objetivo y la arquitectura

**1. ¿Qué vamos a realizar?** Definir un problema real de calidad en un comercio electrónico y el flujo que lo resolverá.

**2. ¿Por qué es necesario?** Una tabla con claves mal interpretadas, fechas incoherentes o importes inválidos puede producir métricas equivocadas aun cuando la consulta SQL esté bien escrita. La calidad debe medirse respecto al contexto del negocio.

**3. ¿Dónde debemos trabajar?** Lee este tutorial, `README.md` y `docs/architecture.mmd` desde VS Code. Ningún comando es necesario todavía.

**4. Implementación.** El objetivo del proyecto es detectar, medir, corregir cuando exista evidencia y documentar anomalías en las relaciones entre pedidos, clientes, artículos, pagos, productos, vendedores, reseñas y ubicaciones. Mantendremos los CSV intactos y sólo dos capas persistentes: `raw` (fuente cargada sin transformación destructiva) y `clean` (registros normalizados). Los errores no corregibles pasarán a un mecanismo auxiliar de cuarentena; `audit` no será una tercera capa de datos.

```text
CSV -> extracción Python+DuckDB -> raw -> 15 reglas SQL -> clean
    -> pruebas críticas PASS/FAIL -> Parquet + reporte de calidad
```

**5. Explicación del código/flujo.** Python coordinará lectura, conexión, auditoría y publicación. DuckDB ejecutará SQL analítico. RAW preservará valores originales y CLEAN contendrá transformaciones. Los CSV y RAW no se sobrescribirán durante la limpieza.

**6. Resultados esperados.** Acuerdo de arquitectura, sin estadísticas inventadas.

**7. Comprobación.** Confirma visualmente que ninguna ruta del proyecto contiene `staging` como capa persistente y que el archivo `architecture.mmd` representa la secuencia correcta.

**8. Problemas frecuentes.** Confundir una *tabla temporal* con una nueva capa de datos, o convertir/eliminar anomalías antes de medirlas. Se evitará con auditoría y documentación.

**9. Resultado del paso.** Objetivo, capas y restricciones definidos.

---

## Paso 2. Comprobar herramientas de Windows 11

**1. ¿Qué vamos a realizar?** Comprobar Miniconda, Python, VS Code y Git; instalar lo que falte.

**2. ¿Por qué es necesario?** Tener varias instalaciones de Python puede hacer que los paquetes se instalen en un intérprete y se ejecute otro.

**3. ¿Dónde debemos trabajar?** Inicio de Windows > **Miniconda Prompt** (no confundir con un notebook de Jupyter). Abre la terminal, no necesitas privilegios de administrador.

**4. Implementación.** Escribe estos comandos uno a uno:

```bat
conda --version
python --version
where python
code --version
git --version
```

Si `conda` no existe, instala Miniconda desde https://www.anaconda.com/download/success ; después **cierra y vuelve a abrir Miniconda Prompt**. Si falta `code`, instala Visual Studio Code desde https://code.visualstudio.com/ y habilita su comando de consola o ábrelo desde el menú Inicio. Para Git: https://git-scm.com/downloads/win .

**5. Explicación.** `conda --version` indica si el gestor está accesible; `python --version` muestra el Python en uso; `where python` revela qué ejecutables encuentra Windows; `code --version` comprueba VS Code; `git --version` comprueba Git.

**6. Resultados esperados.** Mensajes de versión, sin números predefinidos. El requisito del proyecto es **Python 3.11+**, recomendado **3.12**.

**7. Comprobación.** `conda info --envs` debe mostrar al menos `base`.

**8. Problemas frecuentes.** `'conda' no se reconoce`: usar Miniconda Prompt y comprobar instalación. `'code' no se reconoce`: abrir VS Code con el menú Inicio o habilitar PATH. `python` apunta a Microsoft Store: ejecutar desde Miniconda Prompt con el entorno activado.

**9. Resultado del paso.** Herramientas de sistema identificadas.

---

## Paso 3. Crear y abrir la carpeta de trabajo

**1. ¿Qué vamos a realizar?** Descomprimir este paquete de proyecto y abrirlo como raíz en VS Code.

**2. ¿Por qué es necesario?** Las rutas relativas permiten ejecutar el mismo código desde terminal o notebook sin editar nombres de disco ni carpetas de cada equipo.

**3. ¿Dónde debemos trabajar?** Explorador de archivos de Windows y Miniconda Prompt.

**4. Implementación.** Descomprime el ZIP de esta entrega en una ubicación corta, por ejemplo `C:\Proyectos\olist-data-quality`. Abre Miniconda Prompt e ingresa:

```bat
cd /d C:\Proyectos\olist-data-quality
dir
code .
```

Ajusta la ruta del `cd` si elegiste otra. `code .` abre **la carpeta actual**; verifica que la raíz contiene `environment.yml`, `README.md`, `src/`, `data/` y `notebooks/`.

**5. Explicación.** `/d` permite cambiar de unidad al navegar en el símbolo del sistema de Windows. El punto de `code .` significa *carpeta actual*.

**6. Resultados esperados.** Se abre VS Code mostrando la estructura incluida en README.

**7. Comprobación.** En terminal ejecuta `dir environment.yml` y `dir src\config.py`. Ambos deben existir.

**8. Problemas frecuentes.** Ejecutar `cd` a una ruta distinta; abrir la carpeta que contiene el proyecto, en vez del proyecto. Ambas situaciones se comprueban buscando `environment.yml` en la raíz visible.

**9. Resultado del paso.** Proyecto localizado y abierto.

---

## Paso 4. Crear un entorno Conda aislado e instalar dependencias

**1. ¿Qué vamos a realizar?** Crear `olist-quality` con Python 3.12 y todas las librerías declaradas.

**2. ¿Por qué es necesario?** Un entorno aislado evita conflictos con otros proyectos y permite repetir la instalación con versiones compatibles.

**3. ¿Dónde debemos trabajar?** **Miniconda Prompt**, situado en `olist-data-quality`.

**4. Implementación.** Ejecuta:

```bat
conda env create -f environment.yml
conda activate olist-quality
python --version
python -m pip list
```

Para revisar las librerías principales:

```bat
python -c "import duckdb, pandas, pyarrow, matplotlib; print('DuckDB:', duckdb.__version__); print('Pandas:', pandas.__version__); print('PyArrow:', pyarrow.__version__); print('Matplotlib:', matplotlib.__version__)"
```

**5. Explicación.** `environment.yml` pide Python 3.12, pip, DuckDB, Pandas, PyArrow, Matplotlib, JupyterLab, IPyKernel, pytest y Kaggle CLI. `conda activate` cambia el intérprete actual. `python -m pip ...` utiliza el pip asociado a ese intérprete.

**6. Resultados esperados.** Prompt con `(olist-quality)`, versiones imprimibles e importaciones sin errores. Las instalaciones dependen del acceso a repositorios de Conda/PyPI.

**7. Comprobación.** Ejecuta:

```bat
where python
conda info --envs
python -c "import duckdb; print(duckdb.sql('SELECT 1 + 1').fetchall())"
```

La última consulta debe producir `(2,)`.

**8. Problemas frecuentes.** `EnvironmentNameNotFound`: falló la instalación previa; revisar error y repetir. Dependencias sin descargar: revisar internet/proxy y configuración de canales. `ModuleNotFoundError`: confirmar entorno activo y ejecutar `python -m pip install -r requirements.txt` **sólo si faltaron dependencias**. Advertencia de Python 3.10: este proyecto selecciona 3.12, no 3.10.

**9. Resultado del paso.** Entorno de Python aislado y paquetes disponibles.

---

## Paso 5. Configurar VS Code, extensiones y kernel de Jupyter

**1. ¿Qué vamos a realizar?** Preparar la ejecución de `.py` e `.ipynb` sobre el mismo entorno.

**2. ¿Por qué es necesario?** Un notebook puede usar un intérprete distinto al de la consola; eso produce errores aunque el paquete esté instalado.

**3. ¿Dónde debemos trabajar?** Terminal Miniconda Prompt y la interfaz de VS Code.

**4. Implementación.** En Miniconda Prompt con `(olist-quality)` activo:

```bat
code --install-extension ms-python.python
code --install-extension ms-toolsai.jupyter
python -m ipykernel install --user --name olist-quality --display-name "Python (olist-quality)"
jupyter kernelspec list
code .
```

En VS Code: `Ctrl+Shift+P` > **Python: Select Interpreter** > selecciona `olist-quality`. Abre `notebooks/01_data_quality_etl.ipynb`; arriba a la derecha pulsa **Select Kernel** > **Python (olist-quality)**. Ejecuta la primera celda con el botón de reproducción.

**5. Explicación.** IPyKernel es el ejecutor de código Python que utiliza Jupyter. Registrar el kernel hace que el entorno sea seleccionable en notebooks.

**6. Resultados esperados.** El notebook muestra una ruta de `sys.executable` perteneciente al entorno `olist-quality`.

**7. Comprobación.** En una celda del notebook ejecuta `import sys; print(sys.executable)` y compara con `where python` del entorno activado.

**8. Problemas frecuentes.** `Kernel not found`: revisar instalación de IPyKernel, volver a ejecutar `python -m ipykernel install ...` y reiniciar VS Code. `ModuleNotFoundError` dentro del notebook: cambiar kernel. `code` no existe: abrir VS Code manualmente y buscar extensiones *Python* y *Jupyter*.

**9. Resultado del paso.** Terminal y notebook apuntan al mismo entorno.

---

## Paso 6. Crear la base DuckDB persistente

**1. ¿Qué vamos a realizar?** Crear `database/ecommerce.duckdb` con los esquemas `raw`, `clean` y `audit`.

**2. ¿Por qué es necesario?** La base persiste en disco entre sesiones. `raw` y `clean` estarán separados; `audit` almacenará información operativa, no datos transformados como tercera capa.

**3. ¿Dónde debemos trabajar?** Miniconda Prompt, raíz del proyecto; archivo `src/bootstrap.py` si quieres examinar la implementación.

**4. Implementación.** Ejecuta:

```bat
python -m src.bootstrap
```

Para consultar la base sin modificar nada, ejecuta estas instrucciones en una **celda del notebook** `notebooks/01_data_quality_etl.ipynb`:

```python
import duckdb
from src.config import DB_PATH

con = duckdb.connect(str(DB_PATH))
try:
    sql = """SELECT schema_name FROM information_schema.schemata
             WHERE schema_name IN ('raw', 'clean', 'audit')
             ORDER BY schema_name"""
    print(con.execute(sql).fetchall())
finally:
    con.close()
```

**5. Explicación.** `CREATE SCHEMA IF NOT EXISTS` no elimina esquemas existentes; el script cierra su conexión en `finally`. `DB_PATH` apunta siempre a la raíz, independientemente de dónde esté la terminal.

**6. Resultados esperados.** Base `ecommerce.duckdb`, mensajes `[OK]` y **ninguna tabla de pedidos aún**. Las tablas RAW pertenecen a la fase de extracción, no a esta preparación.

**7. Comprobación.** `dir database\ecommerce.duckdb` y `python -m src.bootstrap` por segunda vez. Ambas operaciones deben tener éxito.

**8. Problemas frecuentes.** `ModuleNotFoundError: duckdb`: activar entorno. `IO Error` / archivo bloqueado: cerrar conexiones de otros procesos. Directorio equivocado: utilizar `python -m src.bootstrap` desde la raíz.

**9. Resultado del paso.** Base persistente operativa sin datos simulados.

---

## Paso 7. Descargar los datos de Olist sin modificarlos

**1. ¿Qué vamos a realizar?** Obtener los CSV oficiales de https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce .

**2. ¿Por qué es necesario?** Un análisis reproducible debe poder rastrear cada transformación hasta la fuente original y preservar los archivos originales.

**3. ¿Dónde debemos trabajar?** Miniconda Prompt, raíz del proyecto; o navegador/Explorador de archivos si prefieres descargar manualmente.

**4. Implementación, opción A: Kaggle CLI.** Con `(olist-quality)` activo:

```bat
kaggle --version
kaggle auth login
kaggle datasets download olistbr/brazilian-ecommerce -p data/raw --unzip
```

El segundo comando abre el navegador para autenticarse si la cuenta lo requiere. La opción `--unzip` extrae el archivo descargado en `data/raw/`. Alternativa manual: abrir el enlace, descargar el ZIP, descomprimir y copiar **sólo sus nueve CSV** a `data/raw/` sin guardarlos desde Excel.

**5. Explicación.** Kaggle CLI es la herramienta oficial para descargar datasets. La carpeta `data/raw/` conserva los CSV fuente; el esquema `raw` de DuckDB se poblará en la etapa siguiente, después del diagnóstico del formato.

**6. Resultados esperados.** Nueve archivos `.csv` con los nombres documentados en `src/config.py`. No establecemos un conteo fijo de filas aquí.

**7. Comprobación.** Ejecuta `dir data\raw\*.csv` y revisa el listado. Para la verificación automática de presencia se utilizará `python -m src.verify_setup --require-data`.

**8. Problemas frecuentes.** `401/403` al descargar: iniciar sesión y revisar que aceptaste las condiciones de uso. `kaggle` no reconocido: revisar si el entorno está activo. `0 CSV`: es posible que el ZIP se haya descargado sin descomprimir o en otra ruta. No usar Excel para modificar formato/ceros iniciales.

**9. Resultado del paso.** Fuentes originales disponibles, sin cambios.

---

## Paso 8. Generar el inventario real de las fuentes

**1. ¿Qué vamos a realizar?** Leer encabezados y contar registros y columnas, detectar filas estructuralmente irregulares y registrar SHA-256 de cada CSV.

**2. ¿Por qué es necesario?** Antes de diseñar SQL hay que conocer **columnas reales** y posibles problemas de lectura. Un hash permite comparar si un archivo se alteró accidentalmente.

**3. ¿Dónde debemos trabajar?** Miniconda Prompt. Código en `src/source_inventory.py`.

**4. Implementación.** Ejecuta:

```bat
python -m src.source_inventory
```

Los reportes resultantes serán:

```text
reports/quality_reports/source_inventory.csv
reports/quality_reports/source_inventory.json
```

**5. Explicación.** `csv.reader` interpreta campos entrecomillados, incluso si contienen saltos de línea; no es lo mismo contar líneas físicas con un editor que contar registros CSV. Se verifica que no haya encabezados vacíos o duplicados y se comprueba que todas las filas tengan la cantidad correcta de campos. `hashlib.sha256()` calcula la huella del archivo sin editarlo.

**6. Resultados esperados.** Una tabla con nombre de archivo, número de registros, número de columnas y problemas de anchura, **todos calculados localmente sobre los CSV reales**. En JSON aparecerá también el listado completo de encabezados.

**7. Comprobación.** Abrir `source_inventory.csv` en VS Code y verificar que haya nueve entradas del dataset y que cada nombre corresponda al de `data/raw`. La aparición de filas con anchura distinta es una incidencia a investigar, no una autorización para eliminarlas.

**8. Problemas frecuentes.** `Faltan archivos`: revisar `data/raw`. `UnicodeDecodeError`: confirmar archivos originales/encoding y documentar problema antes de convertirlo. `Encabezados duplicados`: detener y revisar archivo. Lectura lenta: archivo geográfico grande; el análisis recorre sus registros sin cargarlos todos en RAM.

**9. Resultado del paso.** Primer inventario objetivo de fuentes listo para el diseño relacional de Fase 2.

---

## Paso 9. Ejecutar comprobaciones y pruebas automatizadas

**1. ¿Qué vamos a realizar?** Confirmar que el entorno y la preparación son correctos antes de continuar.

**2. ¿Por qué es necesario?** Detectar errores de configuración ahora evita atribuirlos erróneamente a la lógica SQL durante las fases posteriores.

**3. ¿Dónde debemos trabajar?** Miniconda Prompt, raíz del proyecto, entorno activo; también el notebook.

**4. Implementación.** Ejecuta:

```bat
python -m src.verify_setup --require-data
python -m pytest -q tests
```

Abre `notebooks/01_data_quality_etl.ipynb`, selecciona `Python (olist-quality)` y ejecuta todas las celdas desde el principio. Por último, inicia control de versiones si corresponde:

```bat
git init
git status
```

`git status` no debería listar los CSV, Parquet ni la base DuckDB, porque están excluidos mediante `.gitignore`.

**5. Explicación.** `verify_setup` comprueba versión Python, librerías, conexión y presencia de nueve fuentes. `pytest` utiliza CSV **sintéticos de prueba**, jamás confunde esas métricas con observaciones de Olist. El notebook reutiliza módulos `src` para no duplicar código.

**6. Resultados esperados.** `RESULTADO: PASS` si se instalaron las librerías, se abrió DuckDB y están todos los archivos; `pytest` deberá aprobar los tests implementados. Los conteos del inventario dependerán de la fuente real y no se proporcionan de antemano.

**7. Comprobación.** Confirmar archivos `database/ecommerce.duckdb` y `reports/quality_reports/source_inventory.csv/.json` y ejecutar dos veces `python -m src.bootstrap`: la segunda ejecución no debe borrar nada.

**8. Problemas frecuentes.** `pytest` no reconocido: usar `python -m pytest`. `No module named src`: terminal debe estar en la raíz que contiene `src/`. `FAIL` por CSV faltantes: volver al paso 7; por paquetes faltantes: volver al paso 4. No publicar Parquet de datos no validados.

**9. Resultado del paso.** Fase 1 preparada, verificada localmente y lista para examinar la estructura y las relaciones reales en la Fase 2.

---

## Criterios de cierre de la Fase 1

Marcar un requisito como cumplido **sólo tras comprobarlo en tu equipo**:

- [x] Miniconda, VS Code y Git accesibles.
- [x] Entorno `olist-quality` con Python 3.12.
- [x] DuckDB/Pandas/PyArrow/Jupyter instalados.
- [x] Kernel Jupyter seleccionado correctamente.
- [x] Esquemas `raw`, `clean` y `audit` creados.
- [x] Nueve CSV originales en `data/raw/`.
- [x] Inventario JSON/CSV generado mediante inspección real.
- [x] `python -m src.verify_setup --require-data` indica PASS.
- [x] `pytest` termina sin errores.
- [x] Ningún CSV fuente ha sido modificado y no existe STAGING persistente.

## Próxima fase en el diseño del proyecto

Fase 2 empleará el inventario realmente obtenido para definir granularidad, PK/FK y tipos, crear el diagrama entidad-relación y cargar los CSV a tablas `raw.*` sin realizar transformaciones prematuras. **Esa carga aún no está implementada en los archivos de esta Fase 1.**

## Referencias de herramientas

- DuckDB Python: https://duckdb.org/docs/current/clients/python/overview
- Kaggle dataset Olist: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
- Kaggle CLI: https://github.com/Kaggle/kaggle-cli/blob/main/docs/datasets.md
- IPyKernel: https://ipython.readthedocs.io/en/stable/install/kernel_install.html
- VS Code y Jupyter: https://code.visualstudio.com/docs/datascience/jupyter-notebooks
- Miniconda: https://www.anaconda.com/docs/getting-started/miniconda/main
