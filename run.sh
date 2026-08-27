#!/bin/bash

# CETP v1 Quick Run Script
# This script provides easy access to all main features

VENV_PYTHON="venv/bin/python"
VENV_PYTEST="venv/bin/pytest"

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}CETP v1 - Quick Run Menu${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "1. Run Gate Demo (4 verdict scenarios)"
echo "2. Run Online Self-Calibration Demo"
echo "3. Run Test Suite"
echo "4. Start Research Dashboard (web server)"
echo "5. Check Dependencies"
echo "6. Exit"
echo ""
read -p "Select option [1-6]: " choice

case $choice in
    1)
        echo -e "${GREEN}Running Gate Demo...${NC}"
        $VENV_PYTHON gate/cetp_gate.py --demo
        echo ""
        echo -e "${GREEN}Demo complete! Report saved to results/phase8_cetp_gate_demo.md${NC}"
        ;;
    2)
        echo -e "${GREEN}Running Online Learning Demo...${NC}"
        $VENV_PYTHON scripts/analysis/addition7_online_loop.py --demo
        echo ""
        echo -e "${GREEN}Demo complete! Results saved to results/addition7_*.csv${NC}"
        ;;
    3)
        echo -e "${GREEN}Running Test Suite...${NC}"
        $VENV_PYTEST tests/test_e2e.py -v
        ;;
    4)
        echo -e "${GREEN}Starting Research Dashboard...${NC}"
        echo -e "${YELLOW}Open your browser to: http://localhost:8000${NC}"
        echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
        $VENV_PYTHON -m http.server 8000 --directory dashboard
        ;;
    5)
        echo -e "${GREEN}Checking Dependencies...${NC}"
        echo ""
        echo "Python version:"
        $VENV_PYTHON --version
        echo ""
        echo "Installed packages:"
        venv/bin/pip list | grep -E "(numpy|pandas|joblib|scikit-learn|pyyaml|matplotlib|pytest)"
        echo ""
        if [ -f "models/bottleneck_classifier.pkl" ]; then
            echo -e "${GREEN}✓ Model files found${NC}"
        else
            echo -e "${YELLOW}⚠ Model files not found${NC}"
        fi
        ;;
    6)
        echo "Exiting..."
        exit 0
        ;;
    *)
        echo -e "${YELLOW}Invalid option. Please run the script again.${NC}"
        exit 1
        ;;
esac
